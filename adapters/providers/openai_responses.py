"""OpenAI Responses 文本 Provider：协议 `openai_responses`（ADR-039 后续决定，2026-10-09）。

**只按 `text_generation` 的契约调用**：`POST {base_url}/responses`，非流式，结果只按
`TextResponse` 解析。起因：中转（真实案例 cchost.ai + `gpt-6.1-sol`）对 `GET /v1/models`
鉴权通过并列出模型，`POST /v1/chat/completions` 却回 404——Codex 系模型只挂在
Responses 接口上（CC Switch 里这类供应商的「API 格式」就是 OpenAI Responses）。

出网校验、不跟随跳转、Key 来源日志、错误分层、测试连接（只读 `GET /models`）全部继承
:class:`OpenAICompatTextProvider`，这里只换三样：接口路径、请求体、结果解析。

`TextRequest` 的映射（OpenAI Responses Create 官方契约）：

- `system` → `instructions`；`user` → `input`（字符串形式，等价于一条 user 消息）；
- `max_output_tokens` → `max_output_tokens`，低于 16 抬到 16（Responses 拒绝 < 16）；
- `json_mode` → `text.format = {"type": "json_object"}`（不是 Chat 的 `response_format`）；
- `temperature` **不发**：Codex / 推理系模型不接受采样参数，发了会 400；
  平台的结构化输出也不依赖它。

结果解析与终态校验的规则移植自 CC Switch（farion1231/cc-switch，MIT，
commit 2db86e94da13365caae55bb08d09295e31500d21，
`src-tauri/src/proxy/providers/transform_responses.rs` 的
`validate_responses_terminal_status` / `build_anthropic_usage_from_responses` /
`RESPONSES_MIN_MAX_OUTPUT_TOKENS`）。原文是 Rust，按本仓库契约重写为 Python，
许可见根目录 THIRD_PARTY_NOTICES.md。
"""

from __future__ import annotations

import re
from typing import Any

from adapters.providers.base import TextRequest, TextResponse
from adapters.providers.openai_compat import OpenAICompatTextProvider
from apps.api.core.errors import AppError

# 与 `openai_compat` 同口径：上游错误码只收这种形状的短码
_UPSTREAM_CODE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")

# Responses 拒绝 `max_output_tokens` < 16（CC Switch `RESPONSES_MIN_MAX_OUTPUT_TOKENS`）
MIN_MAX_OUTPUT_TOKENS = 16


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def usage_of_responses(payload: dict[str, Any]) -> tuple[int, int, int]:
    """取用量：`input_tokens` / `output_tokens` / `output_tokens_details.reasoning_tokens`。

    有的中转回 Chat 的字段名，退一步读 `prompt_tokens` / `completion_tokens`。
    缺字段记 0 不抛——用量统计不该让一次成功的生成失败（与 `base.usage_of` 同口径）。
    """
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return 0, 0, 0
    tokens_in = _int(usage.get("input_tokens")) or _int(usage.get("prompt_tokens"))
    tokens_out = _int(usage.get("output_tokens")) or _int(usage.get("completion_tokens"))
    detail = usage.get("output_tokens_details") or usage.get("completion_tokens_details")
    reasoning = _int(detail.get("reasoning_tokens")) if isinstance(detail, dict) else 0
    return tokens_in, tokens_out, reasoning


def _output_parts(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    """`output[]` 里 message 的 `output_text` 文本与 `refusal` 文本。推理项、工具项不算正文。"""
    texts: list[str] = []
    refusals: list[str] = []
    output = payload.get("output")
    if not isinstance(output, list):
        return texts, refusals
    for item in output:
        if not isinstance(item, dict) or item.get("type", "message") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            kind = part.get("type")
            if kind in ("output_text", "text") and isinstance(part.get("text"), str):
                texts.append(part["text"])
            elif kind == "refusal" and isinstance(part.get("refusal"), str):
                refusals.append(part["refusal"])
    return texts, refusals


def response_text(payload: dict[str, Any]) -> tuple[str, list[str]]:
    """正文：优先 SDK 风格的顶层 `output_text`，没有再拼 `output[].content[].text`。"""
    texts, refusals = _output_parts(payload)
    top = payload.get("output_text")
    if isinstance(top, str) and top.strip():
        return top, refusals
    return "".join(texts), refusals


class OpenAIResponsesTextProvider(OpenAICompatTextProvider):
    generation_path = "/responses"

    def request_body(self, request: TextRequest) -> dict[str, object]:
        """正式调用的请求体。单独拿出来，测试断言字段映射（尤其是不发 temperature）。"""
        body: dict[str, object] = {
            "model": self.model_id,
            "input": request.user,
            "max_output_tokens": max(request.max_output_tokens, MIN_MAX_OUTPUT_TOKENS),
        }
        if request.system:
            body["instructions"] = request.system
        if request.json_mode:
            body["text"] = {"format": {"type": "json_object"}}
        return body

    async def generate_text(self, request: TextRequest) -> TextResponse:
        payload = await self.post_generation(request)
        self._check_terminal_status(payload)
        text, refusals = response_text(payload)
        tokens_in, tokens_out, reasoning = usage_of_responses(payload)
        if not text.strip():
            if refusals:
                raise AppError(
                    "provider.content.rejected",
                    message=f"自定义端点的 {self.model_id} 拒绝回答：{refusals[0][:200]}",
                    detail={"model_id": self.model_id},
                )
            raise AppError(
                "provider.transient.timeout",
                message=(
                    f"自定义端点的 {self.model_id} 返回空内容 "
                    f"(status={payload.get('status')}, reasoning_tokens={reasoning})"
                ),
                detail={"model_id": self.model_id},
            )
        return TextResponse(
            text=text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model_id=self.model_id,
            reasoning_tokens=reasoning,
        )

    def _check_terminal_status(self, payload: dict[str, Any]) -> None:
        """HTTP 200 不等于生成成功：只有 `status=completed` 或没给 `status`（部分中转不回）
        才读正文。failed / cancelled、带 `error`、`incomplete`（截断）以及 queued / in_progress
        等非终态都报错——没生成完的 output 交给下游只会变成一句解析失败。"""
        status = payload.get("status")
        error = payload.get("error")
        detail: dict[str, object] = {"model_id": self.model_id}
        if isinstance(status, str):
            detail["response_status"] = status
        if status in ("failed", "cancelled") or (error is not None and error != {}):
            message = error.get("message") if isinstance(error, dict) else None
            code = error.get("code") if isinstance(error, dict) else None
            if isinstance(code, str) and _UPSTREAM_CODE.match(code):
                detail["upstream_error_code"] = code
            raise AppError(
                "provider.unavailable",
                message=(
                    f"自定义端点的 {self.model_id} 生成失败（status={status}）："
                    f"{str(message or '')[:300]}"
                ),
                detail=detail,
            )
        if status == "incomplete":
            reason = payload.get("incomplete_details")
            why = reason.get("reason") if isinstance(reason, dict) else None
            if why == "content_filter":
                raise AppError(
                    "provider.content.rejected",
                    message=f"自定义端点的 {self.model_id} 输出被内容策略截断",
                    detail=detail,
                )
            raise AppError(
                "provider.transient.timeout",
                message=(
                    f"自定义端点的 {self.model_id} 输出不完整（reason={why}），"
                    "通常是输出上限不够或推理占满了预算"
                ),
                detail=detail,
            )
        if status is not None and status != "completed":
            # 非流式、未开 background，正常不会拿到非终态；拿到了就是中转行为异常，不当成功
            raise AppError(
                "provider.unavailable",
                message=f"自定义端点的 {self.model_id} 返回了未完成的响应（status={status}）",
                detail=detail,
            )
