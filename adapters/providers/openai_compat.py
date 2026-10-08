"""OpenAI 兼容的文本 Provider：协议 `openai_chat`（ADR-039，05_MODEL_GATEWAY.md §5.2）。

`openai_responses`（`openai_responses.py`）是它的子类：同一套出网校验、错误分层与
测试连接，只换接口路径、请求体与结果解析。

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

import re
from typing import Any

import httpx

from adapters.providers import endpoint_url
from adapters.providers.base import (
    KeySource,
    TextRequest,
    TextResponse,
    signal_key_source,
    usage_of,
)
from apps.api.core.errors import AppError

PROVIDER_ID = "provider.org"


class OpenAICompatTextProvider:
    provider_id = PROVIDER_ID
    #: 正式生成打的接口（相对 base_url）。测试连接的结论要写明"没试调用的是哪个接口"
    generation_path = "/chat/completions"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_id: str,
        # 组织连接永远是用户自己的 Key，没有"平台档"这回事
        key_source: KeySource = KeySource.ORG,
        transport: httpx.AsyncBaseTransport | None = None,
        # 连接的虚拟路由 id（`provider.org:<id>`），只用于"实际拿着哪把 Key 调了谁"的日志
        provider_id: str | None = None,
    ) -> None:
        if provider_id:
            self.provider_id = provider_id
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

    def request_body(self, request: TextRequest) -> dict[str, object]:
        return self.chat_request_body(request)

    async def post_generation(self, request: TextRequest) -> dict[str, Any]:
        """发一次生成请求，返回解析好的 JSON。出网校验、不跟随跳转、错误分层都在这里，
        子类（`openai_responses`）只换接口路径、请求体与结果解析。"""
        self._signal_call()
        await endpoint_url.assert_public_host(self._base_url)
        try:
            async with self._client(180) as client:
                resp = await client.post(
                    f"{self._base_url}{self.generation_path}",
                    headers={**self._auth, "Content-Type": "application/json"},
                    json=self.request_body(request),
                )
        except httpx.TimeoutException as exc:
            raise AppError("provider.transient.timeout", message=f"自定义端点超时：{exc}") from exc
        except httpx.HTTPError as exc:
            raise AppError("provider.unavailable", message=f"自定义端点连不上：{exc}") from exc

        refuse_redirect(resp)
        raise_for_upstream(resp, model_id=self.model_id)
        try:
            payload = resp.json()
        except ValueError as exc:
            raise AppError("provider.unavailable", message="自定义端点返回的不是 JSON") from exc
        if not isinstance(payload, dict):
            raise AppError("provider.unavailable", message="自定义端点返回的不是 JSON 对象")
        return payload

    async def generate_text(self, request: TextRequest) -> TextResponse:
        payload = await self.post_generation(request)
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

        **它验证不了正式生成。** 真实踩过：中转对 `GET /v1/models` 鉴权通过、列出了
        这个模型，`POST /v1/chat/completions` 却回 404——模型只挂在别的接口上。
        所以结论里一律写明"生成接口未试调用、以第一次生成为准"（前端据此画成中性，
        不画成"连接正常"）；免费地验证生成接口没有办法，发一次最小生成就要花用户的钱。
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

        refuse_redirect(resp)
        raise_for_upstream(resp, model_id=self.model_id)
        try:
            listed = [
                str(item.get("id"))
                for item in (resp.json().get("data") or [])
                if isinstance(item, dict)
            ]
        except (ValueError, AttributeError):
            listed = []
        if not listed:
            head = "模型列表接口鉴权通过；端点没有列出模型，请确认模型 ID 拼写"
        elif self.model_id in listed:
            head = f"模型列表接口鉴权通过，列表（{len(listed)} 个）里有 {self.model_id}"
        else:
            head = (
                f"模型列表接口鉴权通过，但列表（{len(listed)} 个）里没有 {self.model_id}，"
                "请核对模型 ID"
            )
        return f"{head}。{generation_unverified(self.generation_path)}"


def generation_unverified(path: str) -> str:
    """测试连接只读了模型列表。"以第一次"是前端 `classifyTest` 判中性的依据，改措辞要同步。"""
    return (
        "只读取了模型列表（GET /models，不发生成请求），没有试调用生成接口 "
        f"POST {path}；该模型能否用这个接口生成，以第一次生成为准"
    )


# 上游错误体里的 `error.code` / `error.type` 只收这种形状的短码，其余一律不进 detail
_UPSTREAM_CODE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


def _upstream_error_code(resp: httpx.Response) -> str | None:
    try:
        payload = resp.json()
    except ValueError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return None
    for key in ("code", "type"):
        value = error.get(key)
        if isinstance(value, str) and _UPSTREAM_CODE.match(value):
            return value
    return None


def failure_detail(resp: httpx.Response, *, model_id: str) -> dict[str, object]:
    """一次失败调用里能帮用户找到"哪里不匹配"的事实：状态码、实际调的接口、上游自己的错误码。

    Gateway 出口（`service._as_byok_error`）按它们分出 `detail.reason`。真实踩过：
    中转回 404 被包成"你的 API Key 调用失败"，用户去换了一把本来没问题的 Key。
    接口只记方法 + 路径（看得出 `/v1/v1` 这类拼接错误），不记主机；正文原文仍只在
    message 里，由出口统一脱敏。
    """
    detail: dict[str, object] = {"http_status": resp.status_code, "model_id": model_id}
    try:
        request = resp.request
    except RuntimeError:  # 测试里直接构造的 Response 没有 request
        request = None
    if request is not None:
        detail["operation"] = f"{request.method} {request.url.path}"
    code = _upstream_error_code(resp)
    if code is not None:
        detail["upstream_error_code"] = code
    return detail


def raise_for_upstream(resp: httpx.Response, *, model_id: str) -> None:
    """状态码 → 错误目录，分法与 DeepSeek 适配器相同，另带 :func:`failure_detail`。"""
    if resp.status_code == 200:
        return
    text = resp.text[:300]
    detail = failure_detail(resp, model_id=model_id)
    if resp.status_code == 429:
        raise AppError("provider.rate_limit.exceeded", message=text, detail=detail)
    if resp.status_code in (401, 402, 403):
        raise AppError(
            "provider.account.insufficient",
            message=f"auth failed (HTTP {resp.status_code}): {text}",
            detail=detail,
        )
    if resp.status_code in (400, 422):
        raise AppError("provider.params.invalid", message=text, detail=detail)
    raise AppError(
        "provider.unavailable", message=f"HTTP {resp.status_code}: {text}", detail=detail
    )


def refuse_redirect(resp: httpx.Response) -> None:
    # 不跟随只是第一步：3xx 本身也要当失败报，不然调用方会拿一个空的
    # 重定向响应去解析 JSON，得到一句莫名其妙的"返回的不是 JSON"。
    if 300 <= resp.status_code < 400:
        raise AppError(
            "provider.params.invalid",
            message=(
                f"自定义端点返回了跳转（HTTP {resp.status_code}），出于安全不跟随，请填写最终地址"
            ),
            detail={"http_status": resp.status_code},
        )
