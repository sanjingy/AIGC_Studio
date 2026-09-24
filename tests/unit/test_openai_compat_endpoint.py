"""文本自定义端点的出网校验与适配契约（05_MODEL_GATEWAY.md §5.2）。

两件事要钉死：

1. **这是全仓库唯一一个用户可控的出网地址**，SSRF 那几条口子（http、内网、
   回环、云元数据、userinfo、DNS rebinding、跳转）每条都要有一个用例把它挡住。
2. **测试连接与正式调用是同一个适配器**：存下的 base_url / 模型 / Key
   就是请求里出现的那三样，一个字符都不换。

零网络：解析函数与 HTTP 传输全部替换。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from adapters.providers import endpoint_url
from adapters.providers.base import KeySource, TextRequest
from adapters.providers.openai_compat import OpenAICompatTextProvider
from apps.api.core.errors import AppError

KEY = "sk-custom-endpoint-key-0123456789"


@pytest.fixture(autouse=True)
def _public_dns() -> Iterator[None]:
    """默认所有域名都解析到一个公网地址；个别用例自己改。"""

    async def _resolve(host: str) -> list[str]:
        del host
        return ["93.184.216.34"]

    endpoint_url.set_resolver(_resolve)
    yield
    endpoint_url.set_resolver(None)


# ------------------------------------------------------------------ 保存时的形状校验


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://api.example.com/v1", "https://api.example.com/v1"),
        ("https://api.example.com/v1/", "https://api.example.com/v1"),
        ("  https://API.Example.com:8443/openai/v1  ", "https://api.example.com:8443/openai/v1"),
        ("https://1.1.1.1/v1", "https://1.1.1.1/v1"),
    ],
)
def test_accepts_public_https(raw: str, expected: str) -> None:
    assert endpoint_url.normalize_base_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "http://api.example.com/v1",  # 明文 http：Key 以明文走公网
        "ftp://api.example.com/v1",
        "javascript:alert(1)",
        "https://user:pw@api.example.com/v1",  # userinfo 会进日志
        "https://api.example.com/v1#frag",
        "https://api.example.com/v1?key=abc",
        "https://localhost/v1",
        "https://foo.localhost/v1",
        "https://127.0.0.1/v1",
        "https://10.0.0.5/v1",
        "https://192.168.1.10/v1",
        "https://172.16.3.4/v1",
        "https://169.254.169.254/latest/meta-data",  # 云元数据
        "https://100.64.0.1/v1",  # CGNAT
        "https://[::1]/v1",
        "https://[::ffff:127.0.0.1]/v1",  # IPv4 映射的回环
        "https://0.0.0.0/v1",
        "https://metadata.google.internal/v1",
        "https://api.example.com:99999/v1",
        "https://api.example.com/v 1",
    ],
)
def test_rejects_unsafe_urls(raw: str) -> None:
    with pytest.raises(AppError) as exc:
        endpoint_url.normalize_base_url(raw)
    assert exc.value.code == "common.validation_failed"


async def test_dns_rebinding_is_refused_at_call_time() -> None:
    """保存时是公网，调用时解析到内网——必须在请求发出前拒绝。"""

    async def _rebind(host: str) -> list[str]:
        del host
        return ["93.184.216.34", "127.0.0.1"]

    endpoint_url.set_resolver(_rebind)
    with pytest.raises(AppError) as exc:
        await endpoint_url.assert_public_host("https://api.example.com/v1")
    assert exc.value.code == "provider.params.invalid"


# ------------------------------------------------------------------ 适配契约


def _provider(handler: Any, **kw: Any) -> OpenAICompatTextProvider:
    return OpenAICompatTextProvider(
        base_url=kw.pop("base_url", "https://llm.example.com/v1/"),
        api_key=KEY,
        model_id=kw.pop("model_id", "qwen2.5-72b-instruct"),
        key_source=KeySource.ORG,
        transport=httpx.MockTransport(handler),
    )


async def test_generation_uses_saved_base_url_model_and_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            },
        )

    resp = await _provider(handler).generate_text(TextRequest(system="s", user="u"))

    assert resp.text == '{"ok": true}'
    assert resp.model_id == "qwen2.5-72b-instruct"
    assert (resp.tokens_in, resp.tokens_out) == (7, 3)
    (req,) = seen
    assert str(req.url) == "https://llm.example.com/v1/chat/completions"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(req.content)
    assert body["model"] == "qwen2.5-72b-instruct"
    assert body["response_format"] == {"type": "json_object"}


async def test_redirect_is_not_followed() -> None:
    """一次 302 就能把请求带回内网——不跟随，并且当失败报。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})

    with pytest.raises(AppError) as exc:
        await _provider(handler).generate_text(TextRequest(system="s", user="u"))
    assert exc.value.code == "provider.params.invalid"
    assert calls == ["https://llm.example.com/v1/chat/completions"]


async def test_upstream_401_maps_to_account_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(401, json={"error": f"bad key {KEY}"})

    with pytest.raises(AppError) as exc:
        await _provider(handler).generate_text(TextRequest(system="s", user="u"))
    # 脱敏在 Gateway 出口做（`_as_byok_error`），这里只要求错误码走对
    assert exc.value.code == "provider.account.insufficient"


async def test_verify_key_lists_models_without_generation_cost() -> None:
    """测试连接只打 `/models`，不发 chat 请求（不花 token）。"""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"data": [{"id": "qwen2.5-72b-instruct"}, {"id": "x"}]})

    message = await _provider(handler).verify_key()
    assert paths == ["/v1/models"]
    assert "包含 qwen2.5-72b-instruct" in message


async def test_constructor_refuses_private_base_url() -> None:
    """测试连接会拿用户刚填、还没进库的地址直接构造——构造处也要挡。"""
    with pytest.raises(AppError):
        _provider(lambda r: httpx.Response(200), base_url="https://10.1.2.3/v1")
