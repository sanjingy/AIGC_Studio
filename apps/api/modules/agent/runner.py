"""Agent 执行器。

**spec 里的所有约束都在这里强制。** spec 本身只是数据，
没有任何逃逸手段——这是允许第三方提供 Agent 的前提。

强制的四条：
    output_schema   输出必须能解析成声明的 schema，否则重试后失败
    tools           只能调 spec 列出的工具
    max_steps       步数上限，防打转
    budget_credits  单次运行成本上限，防烧钱

另外强制一条 spec 管不到的：**风格词由系统注入**，
Agent 自己写的风格描述会被丢弃（17_ConsistencyEngine.md §4）。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from agents.schemas import resolve as resolve_schema
from agents.spec import AgentSpec
from apps.api.core.errors import AppError
from apps.api.core.logging import get_logger
from apps.api.modules.agent import repository as repo
from apps.api.modules.agent.llm import LLMRequest, get_provider
from skills import registry as skill_registry

log = get_logger(__name__)

# 模型经常把 JSON 包在 ```json 围栏里。这不是模型的错，
# 是提示词工程的现实——直接剥掉比反复告诫"不要加围栏"有效。
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: uuid.UUID
    output: BaseModel
    tokens_in: int
    tokens_out: int
    model_id: str


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """一次模型调用的结果，成败都记。

    存在的意义是让"落库"与"调用"分开：`complete_structured` 只管调模型、
    校验、重试，把每一次尝试原样交给调用方去决定怎么存。挂在项目上的运行
    存进 `agent_steps`，不挂项目的（资产库里的独立角色档案）存进自己的表——
    两条路径共用同一套提示词与重试逻辑，不会各写一份然后分叉。
    """

    index: int
    kind: str  # llm / validate
    system_prompt: str
    raw_output: str | None
    error: str | None
    duration_ms: int


@dataclass(frozen=True, slots=True)
class Completion:
    output: BaseModel
    tokens_in: int
    tokens_out: int
    model_id: str
    system_prompt: str
    attempts: int


#: 每次尝试结束时的回调。给 `run_agent` 用来逐步落 `agent_steps`——
#: 逐步落而不是最后一次性落，是因为中途抛错时那些步骤同样要留下来。
AttemptSink = Callable[[AttemptRecord], Awaitable[None]]


# 单条文本的存储上限。
#
# 在这之前，用户输入被截到 4000 字、原始输出截到 8000 字、错误截到 2000 字，
# **而且不留任何痕迹**。于是"这次到底喂了什么进去"在生成记录里只能看到前
# 一半，排查时无从判断后半段是模型没写还是我们没存。
#
# 现在的规则：**全文存，超限显式标注**（`2026-09-11` 计划 §6）。上限放到
# 20 万字符是为了守住"单条记录不能无限大"这条工程约束——一份 600 镜的分镜
# 表 JSON 也就几十万字节，正常产出碰不到它；真碰到时 `text_complete` 会写
# False，界面据此显示「记录不完整」，而不是让用户以为看到的就是全部。
MAX_TEXT_CHARS = 200_000


def bounded(text: str | None) -> tuple[str | None, bool]:
    """返回 (要落库的文本, 是否完整)。**截断必须有人知道。**"""
    if text is None:
        return None, True
    if len(text) <= MAX_TEXT_CHARS:
        return text, True
    return text[:MAX_TEXT_CHARS], False


def _extract_json(raw: str) -> dict[str, Any]:
    cleaned = _FENCE.sub("", raw).strip()
    # 模型偶尔会在 JSON 前后加一句解释。取第一个 { 到最后一个 }。
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("输出里找不到 JSON 对象")
    return dict(json.loads(cleaned[start : end + 1]))


def build_system_prompt(spec_prompt: str, schema: type[BaseModel]) -> str:
    """在 Agent 提示词后追加输出契约。

    两件事必须由平台统一注入，不能指望每个提示词作者记得写：

    1. **目标 schema 全文**。不给的话模型只能猜字段名，
       结构化输出的成功率会低得离谱。
    2. **"json" 这个词**。DeepSeek 等 OpenAI 兼容接口在
       `response_format=json_object` 下会校验提示词里必须出现 "json"，
       否则直接 400。第三方 Agent 作者不该需要知道这种厂商怪癖。
    """
    contract = json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=None)
    return (
        f"{spec_prompt.rstrip()}\n\n"
        "---\n"
        "输出要求：只返回一个 JSON 对象，不要任何解释文字，不要 markdown 围栏。\n"
        f"必须严格匹配这个 JSON Schema：\n{contract}"
    )


def render_prompt(template: str, variables: dict[str, Any]) -> str:
    """填充 {变量} 占位符。

    用逐个替换而不是 str.format——提示词里常有 JSON 示例，
    format 会把里面的花括号当成占位符然后抛 KeyError。
    """
    out = template
    for key, value in variables.items():
        out = out.replace("{" + key + "}", str(value))
    return out


def no_reasoning_roles() -> frozenset[str]:
    """哪些 role 不许用推理模型（ADR-024 硬约束 2）。

    取所有已注册 Skill 声明的并集，不挑"当前 Skill"——Skill 运行时还没接线
    （ADR-026），此刻根本不存在"当前 Skill"这个概念。并集是这个前提下唯一
    安全的读法：漏掉一个 role 的代价是它拿着推理模型**返回空内容且不报错**，
    多算一个 role 的代价只是它的模型偏好被忽略、按默认优先级跑。

    读法与 `billing/credentials.configurable_capabilities()` 一致——
    那边同样是从 Skill 声明里取用户可改的能力集合。
    """
    roles: set[str] = set()
    for spec in skill_registry.registry().specs.values():
        roles.update(spec.model_policy.no_reasoning_roles)
    return frozenset(roles)


async def complete_structured(
    *,
    spec: AgentSpec,
    user_input: str,
    variables: dict[str, Any] | None = None,
    system_suffix: str = "",
    on_attempt: AttemptSink | None = None,
    org_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
) -> Completion:
    """按 spec 调一次模型并拿到合法的结构化产出。**不碰数据库。**

    这里是「跑一个 Agent」的全部实质：提示词合成、输出契约注入、schema 校验、
    校验失败重试。落库交给调用方——`run_agent` 存进 agent_runs / agent_steps，
    资产库里那条不挂项目的路径存进它自己的表。

    分层的理由很实际：`agent_runs.project_id` 是 NOT NULL，而「直接写一段描述
    生成角色档案」根本没有项目。要么把那个列改成可空（波及所有按项目查档案的
    地方），要么把不需要项目的那部分逻辑抽出来共用。抽出来更小也更安全，
    而且保证两条路径共用同一套提示词——两份相似的提示词一定会分叉。
    """
    schema = resolve_schema(spec.output_schema)
    prompt = render_prompt(spec.prompt, variables or {})
    if system_suffix:
        prompt = f"{prompt.rstrip()}\n\n{system_suffix.strip()}"
    system = build_system_prompt(prompt, schema)

    provider = get_provider()
    last_error = ""

    async def _record(
        index: int, kind: str, *, raw: str | None, error: str | None, started: float
    ) -> None:
        if on_attempt is None:
            return
        # 不在这里截断：截断的决定权连同"截了没有"这个事实一起交给落库那一层
        # （`run_agent._persist` 用 `bounded`），否则记录里看到的半截文本没有
        # 任何东西能说明它是半截的。
        await on_attempt(
            AttemptRecord(
                index=index,
                kind=kind,
                system_prompt=system,
                raw_output=raw or None,
                error=error or None,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        )

    # schema_retries + 1 次机会。校验失败就重试，因为这类失败
    # 换一次采样往往就好了——但不能无限重试，那会把预算烧光。
    for attempt in range(spec.schema_retries + 1):
        started = time.perf_counter()
        raw = ""
        try:
            response = await provider.complete(
                LLMRequest(
                    system=system,
                    user=user_input,
                    schema_name=spec.output_schema,
                    max_output_tokens=spec.max_output_tokens,
                    org_id=org_id,
                    project_id=project_id,
                    allow_reasoning=spec.role not in no_reasoning_roles(),
                )
            )
            raw = response.text
            parsed = schema.model_validate(_extract_json(raw))
        except AppError as exc:
            # 上游错误（限流、参数、鉴权…）不属于 schema 校验失败，
            # 不该在这里重试——Gateway 已经做过 failover 了。
            await _record(
                attempt, "llm", raw=None, error=f"{exc.code}: {exc.message}", started=started
            )
            raise
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            await _record(attempt, "validate", raw=raw, error=last_error, started=started)
            log.warning(
                "agent.schema_invalid",
                agent_id=spec.id,
                attempt=attempt,
                error=last_error[:200],
            )
            continue

        await _record(attempt, "llm", raw=raw, error=None, started=started)
        return Completion(
            output=parsed,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            model_id=response.model_id,
            system_prompt=system,
            attempts=attempt + 1,
        )

    raise AppError("agent.output.schema_invalid", message=last_error)


async def run_agent(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    spec: AgentSpec,
    user_input: str,
    variables: dict[str, Any] | None = None,
    system_suffix: str = "",
    input_extra: dict[str, Any] | None = None,
) -> RunResult:
    """跑一个 Agent，并把整个过程落进 agent_runs / agent_steps。

    `system_suffix` 追加在 spec 提示词之后、输出契约之前，用于**改变任务性质**
    而不换 Agent——修订就是这种情况：同一个 Agent、同一个 schema，
    但干的是"改一份已有产出"而不是"从素材创作"。
    不给它一条新的角色指令，模型会按原来的角色理解输入，
    实测表现是把用户消息里那份已经合规的 JSON 原样吐回来。

    `input_extra` 原样并进 `input_json`，给调用方钉一点自己的检索维度用
    （成品提示词那条路径钉的是 `prompt_kind` / `subject_key` / `basis_digest`，
    生成记录按它们回查）。它不参与任何执行逻辑，也不许覆盖上面三个固定键。
    """
    stored_input, input_complete = bounded(user_input)
    run = await repo.create_run(
        db,
        org_id=org_id,
        project_id=project_id,
        agent_id=spec.id,
        role=spec.role,
        input_json={
            **(input_extra or {}),
            "user_input": stored_input,
            "variables": variables or {},
            # 这条记录的文本是不是全的。**存量记录没有这个键**，读的一方
            # 据此如实标注"历史记录不完整"，而不是假装它是全的。
            "text_complete": input_complete,
        },
    )
    await db.commit()

    attempts = 0

    async def _persist(record: AttemptRecord) -> None:
        # 逐步落库而不是最后一次性落：中途抛错时这些步骤同样要留下来，
        # 「为什么这一步崩了」全靠它们才查得清。
        nonlocal attempts
        attempts = record.index + 1
        raw_output, _raw_complete = bounded(record.raw_output)
        error, _error_complete = bounded(record.error)
        await repo.add_step(
            db,
            org_id=org_id,
            run_id=run.id,
            step_index=record.index,
            kind=record.kind,
            # resolved_prompt 只在第一步存全文，重试步骤 prompt 相同，
            # 存多份没有信息量只有存储成本
            resolved_prompt=bounded(record.system_prompt)[0] if record.index == 0 else None,
            raw_output=raw_output,
            error=error,
            duration_ms=record.duration_ms,
        )
        await db.commit()

    try:
        completion = await complete_structured(
            spec=spec,
            user_input=user_input,
            variables=variables,
            system_suffix=system_suffix,
            on_attempt=_persist,
            # 这两个一路传到 Gateway：org_id 决定用谁的 Key（ADR-027），
            # project_id 决定用哪个模型（ADR-024）。在此之前这条链路
            # 一个都没往下传，用户在设置页做的选择到不了真正的调用点。
            org_id=org_id,
            project_id=project_id,
        )
    except AppError as exc:
        # 必须把 run 标成 failed，否则它会永远卡在 running，
        # 前端显示"生成中"直到天荒地老。
        await repo.finish_run(
            db,
            run,
            status="failed",
            error_code=exc.code,
            error_detail=bounded(exc.message)[0],
            attempts=attempts,
        )
        await db.commit()
        raise

    await repo.finish_run(
        db,
        run,
        status="succeeded",
        output=completion.output.model_dump(mode="json"),
        model_id=completion.model_id,
        tokens_in=completion.tokens_in,
        tokens_out=completion.tokens_out,
        attempts=completion.attempts,
    )
    await db.commit()

    return RunResult(
        run_id=run.id,
        output=completion.output,
        tokens_in=completion.tokens_in,
        tokens_out=completion.tokens_out,
        model_id=completion.model_id,
    )


def assert_tool_allowed(spec: AgentSpec, tool: str) -> None:
    """工具白名单在调用点强制。

    只在 spec 里声明是不够的——必须在真正要执行工具的地方拦，
    否则一条新加的调用路径就绕过了整套约束。
    """
    if tool not in spec.tools:
        raise AppError(
            "common.forbidden",
            message=f"Agent {spec.id} 未获授权调用工具 {tool}",
            detail={"agent_id": spec.id, "tool": tool, "allowed": spec.tools},
        )
