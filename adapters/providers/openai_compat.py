"""OpenAI 兼容自定义端点的文本 Provider（05_MODEL_GATEWAY.md §5.2）。

**只按 `text_generation` 的契约调用**：`POST {base_url}/chat/completions`，
结果只按 `TextResponse` 解析。端点自己在 `/models` 里声明了什么（哪怕是
一个叫 `sora-video-1` 的东西）都不采信——那是 §5.2 第 4 条。

测试连接与正式调用走**同一个类**、同一个 base_url 规整、同一道出网校验，
不另写一份探测逻辑：两份迟早分叉，表现是"测试连接通过、真调用 404"。

与 DeepSeek 适配器的差别只有三处，都是因为地址是用户填的：
- 每次请求前重新校验解析出来的地址（DNS rebinding）；
- 不跟随跳转，3xx 当失败报；
- 上游回显里的 Key 仍然在 Gateway 出口统一脱敏
  （`service._as_byok_error` / `probe.redact`），这里不另做一份。
"""

from __future__ import annotations

import httpx

from adapters.providers import endpoint_url
from adapters.providers.base import (
    KeySource,
    TextRequest,
    TextResponse,
    signal_key_source,
    usage_of,
)
from adapters.providers.deepseek import _raise_for_status
from apps.api.core.errors import AppError

PROVIDER_ID = "provider.custom.text"


class OpenAICompatTextProvider:
    provider_id = PROVIDER_ID

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_id: str,
        # 自定义端点永远是用户自己的 Key，没有"平台档"这回事
        key_source: KeySource = KeySource.ORG,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # 构造时就规整一遍：库里的值理论上已经校验过，但这个类也会被
        # "测试连接"直接拿用户刚填的地址构造，那时还没进过库。
        self._base_url = endpoint_url.normalize_base_url(base_url)
        self._api_key = api_key
        self.model_id = model_id
        self.key_source = key_source
        self._transport = transport

    def _signal_call(self) -> None:
        signal_key_source(
            provider_id=self.provider_id, model_id=self.model_id, key_source=self.key_source
        )

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, follow_redirects=False, transport=self._transport)

    @property
    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def chat_request_body(self, request: TextRequest) -> dict[str, object]:
        """正式调用的请求体。单独拿出来，测试断言"存下的模型名真的进了请求"。"""
        body: dict[str, object] = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
        }
        if request.json_mode:
            body["response_format"] = {"type": "json_object"}
        return body

    async def generate_text(self, request: TextRequest) -> TextResponse:
        self._signal_call()
        await endpoint_url.assert_public_host(self._base_url)
        try:
            async with self._client(180) as client:
                resp = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers={**self._auth, "Content-Type": "application/json"},
                    json=self.chat_request_body(request),
                )
        except httpx.TimeoutException as exc:
            raise AppError("provider.transient.timeout", message=f"自定义端点超时：{exc}") from exc
        except httpx.HTTPError as exc:
            raise AppError("provider.unavailable", message=f"自定义端点连不上：{exc}") from exc

        _refuse_redirect(resp)
        _raise_for_status(resp)
        try:
            payload = resp.json()
        except ValueError as exc:
            raise AppError("provider.unavailable", message="自定义端点返回的不是 JSON") from exc
        choice = (payload.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        tokens_in, tokens_out, reasoning = usage_of(payload)
        if not text.strip():
            raise AppError(
                "provider.transient.timeout",
                message=(
                    f"自定义端点的 {self.model_id} 返回空内容 "
                    f"(finish_reason={choice.get('finish_reason')})"
                ),
            )
        return TextResponse(
            text=text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model_id=self.model_id,
            reasoning_tokens=reasoning,
        )

    async def verify_key(self) -> str:
        """测试连接：`GET {base_url}/models`，不产生 token 费用。

        只确认"地址通、Key 被接受"。返回的模型列表**只用来提示**用户填的
        模型 id 在不在里面，不据此改变任何调用行为（§5.2 第 4 条）。
        """
        self._signal_call()
        await endpoint_url.assert_public_host(self._base_url)
        try:
            async with self._client(20) as client:
                resp = await client.get(f"{self._base_url}/models", headers=self._auth)
        except httpx.TimeoutException as exc:
            raise AppError("provider.transient.timeout", message=f"自定义端点超时：{exc}") from exc
        except httpx.HTTPError as exc:
            raise AppError("provider.unavailable", message=f"自定义端点连不上：{exc}") from exc

        _refuse_redirect(resp)
        _raise_for_status(resp)
        try:
            listed = [
                str(item.get("id"))
                for item in (resp.json().get("data") or [])
                if isinstance(item, dict)
            ]
        except (ValueError, AttributeError):
            listed = []
        if not listed:
            return "鉴权通过；端点没有列出模型，请确认模型 ID 拼写"
        if self.model_id in listed:
            return f"鉴权通过，端点列出了 {len(listed)} 个模型，包含 {self.model_id}"
        return f"鉴权通过，但端点列出的 {len(listed)} 个模型里没有 {self.model_id}，请核对模型 ID"


def _refuse_redirect(resp: httpx.Response) -> None:
    # 不跟随只是第一步：3xx 本身也要当失败报，不然调用方会拿一个空的
    # 重定向响应去解析 JSON，得到一句莫名其妙的"返回的不是 JSON"。
    if 300 <= resp.status_code < 400:
        raise AppError(
            "provider.params.invalid",
            message=(
                f"自定义端点返回了跳转（HTTP {resp.status_code}），出于安全不跟随，请填写最终地址"
            ),
        )
