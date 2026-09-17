"""生成记录：把散落的运行、任务与资产收成一条能回放的时间线。

**不新建第二套执行状态**（ADR-008）。这里只做投影，数据仍然来自：

    agent_runs / agent_steps   一次推理：喂了什么、模型原样吐了什么、错在哪
    tasks                      一次出图：最终提示词、来源、状态、产物、错误
    assets.metadata_json       上游有没有改写提示词（`actual_prompt`）

分两层给：**列表只给人能判断的字段**（对象、动作、时间、状态、模型、产物），
**全文只在详情里取**。列表里塞全文会让一个 600 镜的项目一次拉几十兆，而
用户在列表上要做的判断只有"哪一条失败了、点哪一条"。

三件事是这次整改的红线，都落在这个模块里：

1. **失败也要记。** 提示词校验不过、风格词缺失、上下文过期、Provider 报错、
   取消、超时，全都在列表里看得见——只记成功等于"出问题时什么都查不到"。
2. **历史不完整就说不完整。** 这次之前落库的运行把输入截到 4000 字、原始
   输出截到 8000 字且不留痕迹。那些记录**不可能**补回来，所以标
   `incomplete=true`，不假装它是全的，也不用当前档案重新拼一段冒充"当时的词"。
3. **不给用户看不该看的。** 系统提示词（`agent_steps.resolved_prompt`）、
   凭据、认证头、堆栈、本机路径一律不进这个投影，模型的原始输出
   （`agent_steps.raw_output`）也不进——它可能夹着推理过程或把系统提示词
   整段复述回来。失败原因按错误码翻成公开中文（`_public_error`），
   不回显上游原文。

   成品提示词不在此列——那是用户的作品，不是隐藏推理，给全文。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.errors import AppError
from apps.api.modules.agent import service as agent_service
from apps.api.modules.asset import service as asset_service
from apps.api.modules.project import service as project_service
from apps.api.modules.prompting import rules
from apps.api.modules.task import service as task_service

RECORD_AGENT = "agent"
RECORD_IMAGE = "image"
RECORD_TYPES = (RECORD_AGENT, RECORD_IMAGE)

#: 一页最多给多少条。两类记录各取这么多再归并，所以实际扫描量是两倍。
MAX_RECORDS = 200

IMAGE_TASK = "image.generate"

#: 阶段产出 Agent 的中文名。生成记录的标题按它拼——用户不必知道
#: `story.screenplay.v1` 是什么，他要找的是"剧本那一次跑失败了"。
_STAGE_LABEL: dict[str, str] = {
    "router.default.v1": "创作路线",
    "story.plot_index.v1": "情节目录",
    "story.screenplay.v1": "剧本",
    "visual.character.v1": "角色档案",
    "visual.scene.v1": "场景档案",
    "visual.storyboard.v1": "分镜表",
    "story.default.v1": "故事大纲",
    "visual.default.v1": "视觉方案",
    "director.default.v1": "导演决策",
    "qa.default.v1": "质量检查",
}

_SUBJECT_LABEL: dict[str, str] = {
    "character": "角色",
    "scene": "场景",
    "shot": "第 {key} 镜",
    "shot_image": "第 {key} 镜",
    "shot_video": "第 {key} 镜",
}


@dataclass(frozen=True, slots=True)
class RecordSummary:
    id: str
    record_type: str
    subject_kind: str | None
    subject_key: str | None
    title: str
    status: str
    agent_id: str | None
    model_id: str | None
    source: str | None
    created_at: datetime
    finished_at: datetime | None
    error_code: str | None
    asset_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RecordStep:
    """一次尝试。**只有能安全给用户看的字段。**

    `error` 是公开的中文说明，不是上游原文：原文里可能带认证头回显、
    本机路径、上游内部堆栈。`raw_output` 恒为 None，理由见 `_public_error`
    上方那段注释。
    """

    index: int
    kind: str
    duration_ms: int
    error: str | None
    raw_output: str | None


@dataclass(frozen=True, slots=True)
class RecordDetail:
    summary: RecordSummary
    user_input: str | None
    prompt: str | None
    negative_prompt: str | None
    actual_prompt: str | None
    rule_version: str | None
    basis_digest: str | None
    incomplete: bool
    steps: list[RecordStep]
    output: dict[str, Any] | None


#: 尝试这一步失败时给用户看的话。按错误码取，**不回显上游原文**。
#:
#: 原文里出现过什么：Provider 的完整 HTTP 响应（含回显的请求头）、
#: httpx 的连接异常（含本机主机名与端口）、pydantic 的校验堆栈（含仓库内
#: 绝对路径）。这些对用户没有用，对攻击者有用。
#:
#: 取不到就回落到一句通用说明 + 错误码本身——错误码是公开契约
#: （`core/errors.py` 的目录），它本来就会出现在 HTTP 响应里。
_STEP_ERROR_TEXT: dict[str, str] = {
    "agent.output.schema_invalid": "模型返回的内容不符合约定格式",
    "agent.output.truncated": "模型输出被截断，没写完",
    "provider.unavailable": "没有可用的模型",
    "provider.rate_limited": "上游限流，稍后重试",
    "provider.timeout": "上游超时",
    "provider.params.invalid": "请求参数被上游拒绝",
    "provider.content.rejected": "内容被上游安全策略拒绝",
    "prompt.style_tokens.missing": "锁定画风的描述词没有被原样保留",
    "prompt.output.invalid": "产出不符合模板要求",
    "prompt.context.incomplete": "前置内容不全，无法生成",
    "prompt.style.unlocked": "项目还没有锁定画风",
}


def _public_error(error_code: str | None, raw: str | None) -> str | None:
    """把一次失败翻译成能给用户看的一句话。

    有错误码就按码取文案。没有码但确实失败了，也**不回显 `raw`**——
    只说"执行失败"。宁可信息少一点，也不要把堆栈和本机路径漏出去；
    真要排查，那些东西在服务端日志里，而日志是有访问控制的。
    """
    if error_code:
        return _STEP_ERROR_TEXT.get(error_code, f"执行失败（{error_code}）")
    return "执行失败" if raw else None


def _subject_text(kind: str | None, key: str | None) -> str:
    if not kind or not key:
        return ""
    template = _SUBJECT_LABEL.get(kind, "")
    if not template:
        return key
    return template.format(key=key) if "{key}" in template else f"{template} {key}"


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _agent_summary(run: Any) -> RecordSummary:
    payload = _dict(run.input_json)
    kind = str(payload.get("prompt_kind", "") or "") or None
    key = str(payload.get("subject_key", "") or "") or None
    if kind:
        title = rules.KIND_LABEL.get(kind, "提示词")
        if subject := _subject_text(kind, key):
            title = f"{title} · {subject}"
    else:
        title = _STAGE_LABEL.get(run.agent_id, run.agent_id)
    return RecordSummary(
        id=str(run.id),
        record_type=RECORD_AGENT,
        subject_kind=kind,
        subject_key=key,
        title=title,
        status=run.status,
        agent_id=run.agent_id,
        model_id=run.model_id,
        # 文本生成走的是哪条通道（平台 Gateway / BYOK / 本机 CLI）在
        # `agent_runs` 上没有落列。**不猜**：填一个 "api" 会让本机跑的那些
        # 运行在界面上显示成平台在花钱。留 None，界面显示"未记录"。
        source=None,
        created_at=run.created_at,
        finished_at=run.finished_at,
        error_code=run.error_code,
        asset_ids=[],
    )


def _image_summary(task: Any) -> RecordSummary:
    payload = _dict(task.input_json)
    output = _dict(task.output_json)
    kind = str(payload.get("subject_kind", "") or "") or None
    key = str(payload.get("subject_ref", "") or "") or None
    if key is None and payload.get("shot_index") is not None:
        key = str(payload.get("shot_index"))
    subject = _subject_text(kind, key)
    title = f"出图 · {subject}" if subject else "出图"
    asset_ids = [str(a) for a in (output.get("asset_ids") or []) if str(a).strip()]
    return RecordSummary(
        id=str(task.id),
        record_type=RECORD_IMAGE,
        subject_kind=kind,
        subject_key=key,
        title=title,
        status=task.status,
        # 出图任务本身没有 Agent；产出它提示词的那一次运行是**另一条记录**，
        # 靠 `prompt_run_id` 串起来（详情里给）。这里填成提示词 Agent 的 id
        # 会让人以为这条记录就是那次推理。
        agent_id=None,
        model_id=task.model_id or str(output.get("model_id", "") or "") or None,
        source=str(payload.get("image_source", "") or "") or None,
        created_at=task.created_at,
        finished_at=task.finished_at,
        error_code=task.error_code,
        asset_ids=asset_ids,
    )


async def list_records(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, limit: int = 50
) -> list[RecordSummary]:
    """一个项目的生成记录，新的在前。**不调模型，不取全文。**"""
    # 先确认项目属于这个租户。跨租户在这里就 404，后面两条查询也各自带
    # org_id——单点校验加逐条过滤，漏一层还有另一层。
    await project_service.get_project(db, org_id=org_id, project_id=project_id)
    limit = max(1, min(limit, MAX_RECORDS))

    runs = await agent_service.list_runs(db, org_id=org_id, project_id=project_id, limit=limit)
    # 按 type 在数据库里过滤。取回来再筛会漏记录：一个跑过很多次文本阶段的
    # 项目，前 N 条全是别的类型，出图记录一条都剩不下——而那正是要查的。
    tasks, _cursor = await task_service.list_tasks(
        db, org_id=org_id, project_id=project_id, limit=limit, task_type=IMAGE_TASK
    )

    records = [_agent_summary(r) for r in runs]
    records += [_image_summary(t) for t in tasks]
    records.sort(key=lambda r: r.created_at, reverse=True)
    return records[:limit]


def _history_incomplete(payload: dict[str, Any]) -> bool:
    """这条运行的文本能不能证明是全的。

    判据是 `text_complete` 这个键**存不存在**：它是这次整改才开始写的，
    存量记录没有它，而那些记录确实被静默截断过（输入 4000 字、原始输出
    8000 字）。补不回来，所以如实标不完整。
    """
    if "text_complete" not in payload:
        return True
    return not bool(payload.get("text_complete"))


async def _agent_detail(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, record_id: uuid.UUID
) -> RecordDetail:
    run = await agent_service.get_run(db, org_id=org_id, run_id=record_id)
    if run.project_id != project_id:
        # 记录属于这个租户的**另一个**项目。跨项目一律 404，与跨租户同样处理。
        raise AppError("common.not_found", message=f"generation record {record_id}")

    payload = _dict(run.input_json)
    output = _dict(run.output_json)
    steps = await agent_service.get_run_steps(db, org_id=org_id, run_id=record_id)
    is_prompt_run = bool(payload.get("prompt_kind"))

    return RecordDetail(
        summary=_agent_summary(run),
        user_input=payload.get("user_input"),
        # 成品提示词运行的 `prompt` 就是它的产出。阶段产出（剧本、分镜）
        # 没有"最终提示词"这个东西，留 None 而不是拿系统提示词顶上——
        # 那一段是平台的内部变量，不是用户的作品。
        prompt=str(output.get("prompt", "")) if is_prompt_run else None,
        negative_prompt=str(payload.get("negative_prompt", "") or "") or None,
        # 文本生成的上游不返回改写后的提示词。**不拿请求词冒充回传**。
        actual_prompt=None,
        rule_version=str(payload.get("rule_version", "") or "") or None,
        basis_digest=str(payload.get("basis_digest", "") or "") or None,
        incomplete=_history_incomplete(payload),
        steps=[
            RecordStep(
                index=s.step_index,
                kind=s.kind,
                duration_ms=s.duration_ms,
                error=_public_error(run.error_code if s.error else None, s.error),
                # **恒为 None。** 原始输出是模型没被解析过的那一段文本，
                # 里面可能夹着它自己的思考过程（推理模型尤其如此），也可能
                # 把系统提示词整段复述回来。用户要看的是"最终写出了什么"，
                # 那在下面的 `output` 里，是结构化的、校验过的。
                raw_output=None,
            )
            for s in steps
        ],
        output=output or None,
    )


async def _actual_prompt(
    db: AsyncSession, *, org_id: uuid.UUID, asset_ids: list[str]
) -> tuple[str | None, bool]:
    """上游改写后的提示词，以及这份资产的元数据是不是截断过的。

    DashScope 会改写提示词（`actual_prompt`），而改写后的那一段才是这张图
    真正的依据。**上游没回就保持 None**，不拿我们发出去的词冒充回传——
    那会让"模型把我的提示词改了"这个问题永远查不出来。
    """
    for asset_id in asset_ids:
        try:
            asset = await asset_service.get_asset(db, org_id=org_id, asset_id=uuid.UUID(asset_id))
        except (AppError, ValueError):
            continue
        metadata = _dict(asset.metadata_json)
        actual = str(metadata.get("actual_prompt", "") or "").strip()
        truncated = bool(metadata.get("prompt_truncated"))
        if actual:
            return actual, truncated
        if truncated:
            return None, True
    return None, False


async def _image_detail(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, record_id: uuid.UUID
) -> RecordDetail:
    task = await task_service.get_task(db, org_id=org_id, task_id=record_id)
    if task.project_id != project_id or task.type != IMAGE_TASK:
        raise AppError("common.not_found", message=f"generation record {record_id}")

    payload = _dict(task.input_json)
    summary = _image_summary(task)
    actual, truncated = await _actual_prompt(db, org_id=org_id, asset_ids=summary.asset_ids)

    # 这个任务的提示词是不是新链路产出的。旧任务的 `prompt` 是确定性拼接的
    # 结果，没有 `rule_version` 也没有 `basis_digest`——能看到全文，但证明
    # 不了它是按哪一版规则生成的，所以同样标不完整。
    rule_version = str(payload.get("rule_version", "") or "") or None
    return RecordDetail(
        summary=summary,
        # 出图任务没有"用户输入"这一层：它的输入就是提示词本身。
        user_input=None,
        prompt=str(payload.get("prompt", "") or "") or None,
        negative_prompt=str(payload.get("negative_prompt", "") or "") or None,
        actual_prompt=actual,
        rule_version=rule_version,
        basis_digest=str(payload.get("basis_digest", "") or "") or None,
        incomplete=truncated or rule_version is None,
        # 出图任务不经过 Agent，没有推理步骤。空列表而不是造一条假的。
        steps=[],
        output=_dict(task.output_json) or None,
    )


async def get_record(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    record_type: str,
    record_id: uuid.UUID,
) -> RecordDetail:
    """一条生成记录的全文详情。**不调模型。**"""
    await project_service.get_project(db, org_id=org_id, project_id=project_id)
    if record_type == RECORD_AGENT:
        return await _agent_detail(db, org_id=org_id, project_id=project_id, record_id=record_id)
    if record_type == RECORD_IMAGE:
        return await _image_detail(db, org_id=org_id, project_id=project_id, record_id=record_id)
    raise AppError(
        "common.validation_failed",
        message=f"未知的记录类型 {record_type!r}",
        detail={"allowed": list(RECORD_TYPES)},
    )
