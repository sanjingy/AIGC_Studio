"""成品提示词：准备、复用、校验、回查。

这是 ADR-036 的落地点。四类提示词（角色立绘、场景四视图、单镜首帧、
逐镜视频）都走同一条路：

    组装上下文 → 跑 Agent → 校验风格词与模板 → 落 agent_runs

**没有第二条路。** 生产出图端点也走这里，不再调 `compose` 的确定性拼接，
校验不过就不建任务、不预扣、不出图，也不退回旧拼接"修好"它——退回一次，
用户拿到的是一张与全片画风无关的图，而且没有任何地方会告诉他。

**运行记录复用 `agent_runs` / `agent_steps`**（ADR-008：执行状态只认
`tasks.status`，这里存的是推理过程，不是第二份任务状态）。检索维度钉在
`input_json.prompt_kind` 与 `input_json.subject_key` 两个键上。

**GET 一律零推理。** 读一份已经准备好的提示词、判断它过没过期，都不调模型。
用户显式点"准备提示词"或点"生成"时才会有新的推理发生。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.errors import AppError
from apps.api.core.logging import get_logger
from apps.api.modules.agent import service as agent_service
from apps.api.modules.project import service as project_service
from apps.api.modules.prompting import context as context_mod
from apps.api.modules.prompting import rules

log = get_logger(__name__)

#: 找可复用的历史版本时往回翻几条。翻太多没有意义——上下文一变摘要就变，
#: 能命中的几乎总是最近那几条。
_REUSE_LOOKBACK = 20


@dataclass(frozen=True, slots=True)
class Prompt:
    """一份准备好的提示词，对外视图。"""

    run_id: uuid.UUID
    kind: str
    subject_key: str
    prompt: str
    negative_prompt: str
    basis_digest: str
    stale: bool
    rule_version: str
    agent_id: str
    model_id: str | None
    created_at: datetime


def _output_of(run: Any) -> dict[str, Any]:
    output = run.output_json
    return dict(output) if isinstance(output, dict) else {}


def _input_of(run: Any) -> dict[str, Any]:
    payload = run.input_json
    return dict(payload) if isinstance(payload, dict) else {}


def _as_prompt(run: Any, *, stale: bool) -> Prompt:
    payload = _input_of(run)
    output = _output_of(run)
    return Prompt(
        run_id=run.id,
        kind=str(payload.get("prompt_kind", "")),
        subject_key=str(payload.get("subject_key", "")),
        prompt=str(output.get("prompt", "")),
        negative_prompt=str(payload.get("negative_prompt", "") or ""),
        basis_digest=str(payload.get("basis_digest", "")),
        stale=stale,
        rule_version=str(payload.get("rule_version", "")),
        agent_id=run.agent_id,
        model_id=run.model_id,
        created_at=run.created_at,
    )


async def _current_digest(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, kind: str, subject_key: str
) -> str | None:
    """当前上下文的摘要。算不出来时返回 None。

    算不出来有两种情况：上游产出被改没了（角色档案重跑、场景被删），或者
    前置条件本来就不满足。两种在读取路径上都当成**过期**处理，而不是把
    409 抛给一次只读查询——用户点开的是"看看当时用的是什么词"，
    那份历史提示词仍然应该看得到。
    """
    try:
        basis = await context_mod.build(
            db,
            org_id=org_id,
            project_id=project_id,
            kind=kind,
            subject_key=subject_key,
            # **只读**：这是 GET 路径，算摘要不该顺手把档案冻进库里。
            read_only=True,
        )
    except AppError as exc:
        log.info(
            "prompting.digest_unavailable",
            project_id=str(project_id),
            kind=kind,
            subject_key=subject_key,
            error_code=exc.code,
        )
        return None
    return basis.digest


async def latest(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    kind: str,
    subject_key: str,
) -> Prompt | None:
    """这个对象最近一次准备好的提示词。没有就 None。**不调模型，也不写库。**"""
    if kind not in rules.KINDS:
        raise AppError(
            "common.validation_failed",
            message=f"未知的提示词类型 {kind!r}",
            detail={"allowed": list(rules.KINDS)},
        )
    # 项目归属先判掉。放在这里而不是等 `_current_digest` 里的 `build`：
    # 没有任何运行记录时下面那一句就 `return None` 了，`build` 根本跑不到，
    # 于是别的租户的项目 id 会拿到 200 + null——那等于确认"这个项目不存在
    # 任何提示词"，而正确答案是"这个项目与你无关"。跨租户一律 404。
    await project_service.get_project(db, org_id=org_id, project_id=project_id)
    runs = await agent_service.list_prompt_runs(
        db, org_id=org_id, project_id=project_id, kind=kind, subject_key=subject_key, limit=1
    )
    if not runs:
        return None
    run = runs[0]
    digest = await _current_digest(
        db, org_id=org_id, project_id=project_id, kind=kind, subject_key=subject_key
    )
    stored = str(_input_of(run).get("basis_digest", ""))
    return _as_prompt(run, stale=digest is None or digest != stored)


async def _reusable(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    basis: context_mod.Basis,
    instruction: str,
) -> Any | None:
    """同样的输入已经准备过一份合格的提示词了吗。

    判据是 **(上下文摘要, 用户这次的额外要求)** 两项都相同。摘要相同说明
    上游一个字都没变；额外要求也要比，否则用户上次写了"给他一把伞"、
    这次什么都没写，会拿回那把伞而且不知道为什么。

    复用是为了不重复花一次推理，不是为了省事：命不中就老老实实再跑一次。
    """
    runs = await agent_service.list_prompt_runs(
        db,
        org_id=org_id,
        project_id=project_id,
        kind=basis.kind,
        subject_key=basis.subject_key,
        limit=_REUSE_LOOKBACK,
    )
    for run in runs:
        payload = _input_of(run)
        if str(payload.get("basis_digest", "")) != basis.digest:
            continue
        if str(payload.get("instruction", "") or "") != instruction:
            continue
        if not str(_output_of(run).get("prompt", "")).strip():
            continue
        return run
    return None


def _verify(basis: context_mod.Basis, output: dict[str, Any]) -> None:
    """风格词与模板校验。不合格就抛，**绝不放行也绝不兜底**。

    两类问题分开报：风格词缺失是画风漂移（ADR-036 第 4 条），模板不合格是
    结构不对。用户看到的文案和该做的动作不一样，混成一个错误码就分不清
    "重新生成一次就好"和"这个 Agent 的提示词出问题了"。
    """
    prompt = str(output.get("prompt", ""))
    if problems := rules.check_style(prompt, style=basis.style):
        raise AppError(
            "prompt.style_tokens.missing",
            message="；".join(problems),
            detail={"kind": basis.kind, "subject_key": basis.subject_key, "problems": problems},
        )
    if problems := rules.check_output(basis.kind, output, basis.context):
        raise AppError(
            "prompt.output.invalid",
            message="；".join(problems),
            detail={"kind": basis.kind, "subject_key": basis.subject_key, "problems": problems},
        )


def _user_input(basis: context_mod.Basis, instruction: str) -> str:
    """送给模型的那段文本：上下文 JSON + 用户这次的额外要求。

    JSON 而不是自然语言拼接：上下文里有嵌套的档案、光照状态列表和锚点，
    拼成散文一定会丢结构，而丢掉的多半正是"这个字段到底属于哪个角色"。
    """
    payload = {**basis.context, "instruction": instruction}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)


async def _prepare(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    basis: context_mod.Basis,
    instruction: str,
) -> Prompt:
    """拿着已经组装好的上下文准备一份提示词。**这是唯一会发生新推理的地方。**

    收 `basis` 而不是自己再组装一遍：出图那条路径已经组过一次，重组要再跑
    一轮档案同步与提交，白花的是数据库而不是推理，但白花就是白花。
    """
    kind, subject_key = basis.kind, basis.subject_key
    if (
        existing := await _reusable(
            db, org_id=org_id, project_id=project_id, basis=basis, instruction=instruction
        )
    ) is not None:
        log.info(
            "prompting.reused",
            project_id=str(project_id),
            kind=kind,
            subject_key=subject_key,
            run_id=str(existing.id),
        )
        return _as_prompt(existing, stale=False)

    agent_id = rules.AGENT_OF[basis.kind]
    result = await agent_service.run_for_project(
        db,
        org_id=org_id,
        project_id=project_id,
        agent_id=agent_id,
        user_input=_user_input(basis, instruction),
        input_extra={
            "prompt_kind": basis.kind,
            "subject_key": basis.subject_key,
            "basis_digest": basis.digest,
            "rule_version": rules.RULE_VERSION,
            "instruction": instruction,
            "negative_prompt": basis.negative_prompt,
        },
    )
    output = result.output.model_dump(mode="json")
    try:
        _verify(basis, output)
    except AppError as exc:
        # 运行留在库里并标成 failed：删掉它用户就只看到"生成失败"，
        # 查不到模型到底写了什么，也就永远不知道是哪个词被改写了。
        await agent_service.fail_run(
            db,
            org_id=org_id,
            run_id=result.run_id,
            error_code=exc.code,
            error_detail=exc.message,
        )
        log.warning(
            "prompting.rejected",
            project_id=str(project_id),
            kind=kind,
            subject_key=subject_key,
            run_id=str(result.run_id),
            error_code=exc.code,
        )
        raise

    run = await agent_service.get_run(db, org_id=org_id, run_id=result.run_id)
    log.info(
        "prompting.prepared",
        project_id=str(project_id),
        kind=kind,
        subject_key=subject_key,
        run_id=str(result.run_id),
        model_id=result.model_id,
    )
    return _as_prompt(run, stale=False)


#: 前置失败里**该留一条记录**的那些错误码。
#:
#: 判据是"用户做得了点什么"：内容不全、没锁画风、点名的那份词过期了，
#: 三条都是他能去修的，所以他需要在日志里找得到这一次。
#:
#: 404（对象不属于这个项目/租户）不在里面：那种情况下写一条记录等于确认
#: "这个 id 存在过"，而跨租户一律 404 的全部意义就是不确认这件事。
_RECORDED_FAILURES = (
    "prompt.context.incomplete",
    "prompt.style.unlocked",
    "prompt.style_tokens.missing",
    "prompt.output.invalid",
    "prompt.run.stale",
    "prompt.run.mismatch",
    "consistency.profile.missing",
    "provider.unavailable",
)


async def _record_failure(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    kind: str,
    subject_key: str,
    instruction: str,
    exc: AppError,
) -> None:
    """把一次"还没轮到模型就失败了"记进生成记录。**只在写入路径上调。**

    没有它，这几类失败在日志页是彻底看不见的：`build` 在 `agent_runs` 那一行
    创建之前就抛了，于是用户看到界面报红、点开生成记录却什么都没有。

    **GET 不走这里。** 只读路径失败了不该留痕——用户只是点开看了一眼，
    给他攒一串失败记录，真正的失败反而被淹掉。

    这里自己吞异常：记录写不进去（库连不上、并发冲突）不该把原始的业务
    错误盖掉——用户要看的是"为什么生成不了"，不是"为什么日志没写成"。
    """
    if exc.code not in _RECORDED_FAILURES:
        return
    try:
        await agent_service.record_failed_prompt_run(
            db,
            org_id=org_id,
            project_id=project_id,
            agent_id=rules.AGENT_OF.get(kind, ""),
            kind=kind,
            subject_key=subject_key,
            error_code=exc.code,
            error_detail=exc.message,
            input_extra={"rule_version": rules.RULE_VERSION, "instruction": instruction},
        )
    except Exception:  # 记录写不进去绝不能盖掉业务错误
        log.warning(
            "prompting.failure_record_failed",
            project_id=str(project_id),
            kind=kind,
            subject_key=subject_key,
            error_code=exc.code,
        )


async def _build_or_record(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    kind: str,
    subject_key: str,
    instruction: str,
) -> context_mod.Basis:
    """组装上下文；失败时先留一条可查的记录再把错误抛出去。"""
    try:
        return await context_mod.build(
            db, org_id=org_id, project_id=project_id, kind=kind, subject_key=subject_key
        )
    except AppError as exc:
        await _record_failure(
            db,
            org_id=org_id,
            project_id=project_id,
            kind=kind,
            subject_key=subject_key,
            instruction=instruction,
            exc=exc,
        )
        raise


async def prepare(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    kind: str,
    subject_key: str,
    instruction: str = "",
) -> Prompt:
    """显式准备一份提示词（`POST /projects/{id}/prompts/{kind}/{subject_key}`）。

    同样的输入已经有一份合格产出时直接复用，不重复调模型。
    """
    instruction = (instruction or "").strip()
    basis = await _build_or_record(
        db,
        org_id=org_id,
        project_id=project_id,
        kind=kind,
        subject_key=subject_key,
        instruction=instruction,
    )
    return await _prepare(
        db,
        org_id=org_id,
        project_id=project_id,
        basis=basis,
        instruction=instruction,
    )


async def _pinned(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    basis: context_mod.Basis,
    prompt_run_id: uuid.UUID,
) -> Prompt:
    """用户点名要用的那一份。四道闸，一道都不能省。"""
    run = await agent_service.get_run(db, org_id=org_id, run_id=prompt_run_id)
    if run.project_id != project_id:
        # 跨项目与跨租户一样只给 404，403 会确认资源存在
        raise AppError("common.not_found", message=f"agent run {prompt_run_id}")

    payload = _input_of(run)
    if (
        str(payload.get("prompt_kind", "")) != basis.kind
        or str(payload.get("subject_key", "")) != basis.subject_key
    ):
        raise AppError(
            "prompt.run.mismatch",
            message="指定的提示词不是这个对象的",
            detail={
                "expected": {"kind": basis.kind, "subject_key": basis.subject_key},
                "actual": {
                    "kind": payload.get("prompt_kind"),
                    "subject_key": payload.get("subject_key"),
                },
            },
        )
    if run.status != "succeeded":
        raise AppError(
            "prompt.run.mismatch",
            message="指定的提示词没有成功生成，不能用来出图",
            detail={"status": run.status, "error_code": run.error_code},
        )
    if str(payload.get("basis_digest", "")) != basis.digest:
        raise AppError(
            "prompt.run.stale",
            message="指定的提示词依据的内容已经改过了，请重新准备",
            detail={"prompt_run_id": str(prompt_run_id)},
        )

    # 再校验一次：规则可能在这份提示词准备之后收紧过，而 `rule_version`
    # 已经进了摘要，按说这时摘要就对不上了。留着这一道是因为它便宜，
    # 而"拿着一份不合格的词去出图"的代价不便宜。
    _verify(basis, _output_of(run))
    return _as_prompt(run, stale=False)


async def resolve_for_render(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    kind: str,
    subject_key: str,
    prompt_run_id: uuid.UUID | None = None,
    instruction: str = "",
) -> tuple[Prompt, context_mod.Basis]:
    """出图要用的提示词 + 它依据的上下文。

    没点名时自动准备/复用，老前端那两个不带请求体的按钮因此照常能用——
    但走的是新 Agent，不是旧拼接。
    """
    instruction = (instruction or "").strip()
    basis = await _build_or_record(
        db,
        org_id=org_id,
        project_id=project_id,
        kind=kind,
        subject_key=subject_key,
        instruction=instruction,
    )
    if prompt_run_id is not None:
        try:
            pinned = await _pinned(
                db, org_id=org_id, project_id=project_id, basis=basis, prompt_run_id=prompt_run_id
            )
        except AppError as exc:
            # 点名的那一份过期了 / 不是这个对象的。这也要留痕：用户点的是
            # "用我刚才看的那版"，而它为什么用不了，只有这条记录说得清。
            await _record_failure(
                db,
                org_id=org_id,
                project_id=project_id,
                kind=kind,
                subject_key=subject_key,
                instruction=instruction,
                exc=exc,
            )
            raise
        return pinned, basis

    prompt = await _prepare(
        db,
        org_id=org_id,
        project_id=project_id,
        basis=basis,
        instruction=instruction,
    )
    return prompt, basis
