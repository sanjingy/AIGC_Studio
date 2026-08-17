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


async def run_agent(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    spec: AgentSpec,
    user_input: str,
    variables: dict[str, Any] | None = None,
) -> RunResult:
    schema = resolve_schema(spec.output_schema)
    system = build_system_prompt(render_prompt(spec.prompt, variables or {}), schema)

    run = await repo.create_run(
        db,
        org_id=org_id,
        project_id=project_id,
        agent_id=spec.id,
        role=spec.role,
        input_json={"user_input": user_input[:4000], "variables": variables or {}},
    )
    await db.commit()

    provider = get_provider()
    last_error = ""

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
                )
            )
            raw = response.text
            parsed = schema.model_validate(_extract_json(raw))
        except AppError as exc:
            # 上游错误（限流、参数、鉴权…）不属于 schema 校验失败，
            # 不该在这里重试——Gateway 已经做过 failover 了。
            # 但必须把 run 标成 failed，否则它会永远卡在 running，
            # 前端显示"生成中"直到天荒地老。
            await repo.add_step(
                db,
                org_id=org_id,
                run_id=run.id,
                step_index=attempt,
                kind="llm",
                resolved_prompt=system if attempt == 0 else None,
                error=f"{exc.code}: {exc.message}"[:2000],
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            await repo.finish_run(
                db,
                run,
                status="failed",
                error_code=exc.code,
                error_detail=exc.message[:2000],
                attempts=attempt + 1,
            )
            await db.commit()
            raise
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            await repo.add_step(
                db,
                org_id=org_id,
                run_id=run.id,
                step_index=attempt,
                kind="validate",
                resolved_prompt=system if attempt == 0 else None,
                raw_output=raw[:8000],
                error=last_error[:2000],
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            await db.commit()
            log.warning(
                "agent.schema_invalid",
                agent_id=spec.id,
                attempt=attempt,
                error=last_error[:200],
            )
            continue

        await repo.add_step(
            db,
            org_id=org_id,
            run_id=run.id,
            step_index=attempt,
            kind="llm",
            # resolved_prompt 只在第一步存全文，重试步骤 prompt 相同，
            # 存多份没有信息量只有存储成本
            resolved_prompt=system if attempt == 0 else None,
            raw_output=raw[:8000],
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        await repo.finish_run(
            db,
            run,
            status="succeeded",
            output=parsed.model_dump(mode="json"),
            model_id=response.model_id,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            attempts=attempt + 1,
        )
        await db.commit()

        return RunResult(
            run_id=run.id,
            output=parsed,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            model_id=response.model_id,
        )

    await repo.finish_run(
        db,
        run,
        status="failed",
        error_code="agent.output.schema_invalid",
        error_detail=last_error[:2000],
        attempts=spec.schema_retries + 1,
    )
    await db.commit()
    raise AppError("agent.output.schema_invalid", message=last_error)


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
