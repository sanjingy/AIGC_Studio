"""DeepSeek 文本 Provider（OpenAI 兼容）。

实测要点（2026-08-17）：

- 模型是 `deepseek-v4-flash` / `deepseek-v4-pro` / `deepseek-chat`
- **V4 是推理模型，思考 token 计入输出预算。** 预算给小了会返回空内容：
  实测 max_tokens=64 时 flash 产出 reasoning_tokens=64、content='' ——
  不报错，就是空的。所以这里对空内容显式报错并允许重试。
- 同样的结构化任务，`deepseek-chat` 用 11 个 completion token，
  V4 要 60 个（含推理）。Router 分类、schema 填充这类活儿用 chat 更划算。
"""

from __future__ import annotations

import httpx

from adapters.providers.base import TextRequest, TextResponse, usage_of
from apps.api.core.errors import AppError
from apps.api.core.logging import get_logger

log = get_logger(__name__)

BASE_URL = "https://api.deepseek.com"

# 推理模型必须给足输出预算，否则思考 token 会把内容挤没。
# 这是可靠性下限，不是定价参数——真实单价在 model_pricing 表里。
_MIN_TOKENS_FOR_REASONING = 2048
_REASONING_MODELS = frozenset({"deepseek-v4-flash", "deepseek-v4-pro"})


class DeepSeekProvider:
    provider_id = "provider.deepseek"

    def __init__(self, *, api_key: str, model_id: str = "deepseek-chat") -> None:
        self._api_key = api_key
        self.model_id = model_id

    async def generate_text(self, request: TextRequest) -> TextResponse:
        max_tokens = request.max_output_tokens
        if self.model_id in _REASONING_MODELS:
            max_tokens = max(max_tokens, _MIN_TOKENS_FOR_REASONING)

        body: dict[str, object] = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "max_tokens": max_tokens,
            "temperature": request.temperature,
        }
        if request.json_mode:
            body["response_format"] = {"type": "json_object"}

        try:
            async with httpx.AsyncClient(timeout=180) as client:
                resp = await client.post(
                    f"{BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise AppError(
                "provider.transient.timeout", message=f"deepseek timeout: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise AppError("provider.unavailable", message=f"deepseek: {exc}") from exc

        _raise_for_status(resp)

        payload = resp.json()
        choice = (payload.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        tokens_in, tokens_out, reasoning = usage_of(payload)

        if not text.strip():
            # 推理模型把预算烧在思考上、一个字都没输出。
            # 静默返回空串会让下游报"JSON 解析失败"，排查方向完全错。
            raise AppError(
                "provider.transient.timeout",
                message=(
                    f"{self.model_id} 返回空内容 "
                    f"(finish_reason={choice.get('finish_reason')}, "
                    f"reasoning_tokens={reasoning}, max_tokens={max_tokens})"
                ),
            )

        return TextResponse(
            text=text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model_id=self.model_id,
            reasoning_tokens=reasoning,
        )


def _raise_for_status(resp: httpx.Response) -> None:
    """把上游状态码映射到我们的错误目录（21_ErrorTaxonomy.md）。

    映射到哪个错误码决定了重试、换 Provider 和退款行为，
    所以这里不能笼统地抛一个通用错误。
    """
    if resp.status_code == 200:
        return

    detail = resp.text[:300]
    if resp.status_code == 429:
        raise AppError("provider.rate_limit.exceeded", message=detail)
    if resp.status_code == 402:
        # 平台自己在上游欠费。用户无责，必须全退并告警。
        raise AppError("provider.account.insufficient", message=detail)
    if resp.status_code in (400, 422):
        raise AppError("provider.params.invalid", message=detail)
    if resp.status_code in (401, 403):
        raise AppError("provider.account.insufficient", message=f"auth failed: {detail}")
    raise AppError("provider.unavailable", message=f"HTTP {resp.status_code}: {detail}")
