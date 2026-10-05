"""组织连接的出图链路：`openai_images` 适配器、预设文件、结果下载的出网校验（ADR-039）。

要钉死的几件事：

1. **两种返回都收**：`url` 与 `b64_json`（含 data URL 前缀），顺序与 `revised_prompt` 对齐；
   上游没回实际提示词就记空串，不拿请求词冒充。
2. **只发契约里的字段**：不发 `response_format` / `negative_prompt` / `seed`；尺寸换成
   `1024x1024`；预设的请求体覆盖只认白名单键。
3. **出网三处都挡**：构造时（Base URL 形状）、调用前（DNS 解析到内网）、结果下载
   （上游回传的图片地址是用户可控地址：http / 私网 / 回环 / 元数据 / 凭据 / 跳转）。
4. **预设是数据不是代码**：未知字段、价格字段、白名单外协议、非 https / 私网地址、
   重复 id、非法覆盖键，加载时整份拒绝。

零网络：DNS 解析与 HTTP 传输全部替换。
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from adapters.providers import endpoint_url
from adapters.providers.base import ImageRequest, KeySource
from adapters.providers.openai_images import OpenAIImagesProvider, clean_overrides, to_openai_size
from apps.api.core.errors import AppError
from apps.api.modules.gateway import catalog, presets
from worker.jobs import generation

KEY = "sk-image-connection-key-0123456789"
BASE = "https://img.example.com/api/v3"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


@pytest.fixture(autouse=True)
def _public_dns() -> Iterator[None]:
    """默认所有域名都解析到一个公网地址；个别用例自己改。"""

    async def _resolve(host: str) -> list[str]:
        del host
        return ["93.184.216.34"]

    endpoint_url.set_resolver(_resolve)
    yield
    endpoint_url.set_resolver(None)


def _provider(handler: Any, **kw: Any) -> OpenAIImagesProvider:
    return OpenAIImagesProvider(
        base_url=kw.pop("base_url", BASE + "/"),
        api_key=KEY,
        model_id=kw.pop("model_id", "doubao-seedream-5-0-pro-260628"),
        transport=httpx.MockTransport(handler),
        **kw,
    )


def _recording(responses: list[httpx.Response]) -> tuple[list[httpx.Request], Any]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses[min(len(seen), len(responses)) - 1]

    return seen, handler


# ------------------------------------------------------------------ 适配器：请求


async def test_request_carries_only_contract_fields() -> None:
    seen, handler = _recording(
        [httpx.Response(200, json={"data": [{"url": "https://cdn.example.com/a.png"}]})]
    )
    provider = _provider(handler, request_overrides={"watermark": False})
    result = await provider.generate_image(
        ImageRequest(prompt="一只猫，水墨", negative_prompt="模糊", size="1024*1024", seed=7)
    )

    (req,) = seen
    assert str(req.url) == f"{BASE}/images/generations"
    assert req.method == "POST"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(req.content)
    assert body == {
        "model": "doubao-seedream-5-0-pro-260628",
        "prompt": "一只猫，水墨",
        "size": "1024x1024",
        "watermark": False,
    }
    for absent in ("response_format", "negative_prompt", "seed", "n"):
        assert absent not in body
    assert result.urls == ["https://cdn.example.com/a.png"]
    assert result.inline == []
    assert result.model_id == "doubao-seedream-5-0-pro-260628"
    # 上游没回 revised_prompt：记空串，不拿请求词冒充
    assert result.actual_prompts == [""]
    assert provider.key_source is KeySource.ORG


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1024*1024", "1024x1024"), ("1280×720", "1280x720"), ("2048x2048", "2048x2048")],
)
def test_size_is_converted_to_openai_form(raw: str, expected: str) -> None:
    assert to_openai_size(raw) == expected


def test_overrides_are_whitelisted() -> None:
    assert clean_overrides({"watermark": False}) == {"watermark": False}
    assert clean_overrides(None) == {}
    for bad in ({"response_format": "url"}, {"prompt": "x"}, {"model": "y"}):
        with pytest.raises(ValueError):
            clean_overrides(bad)
    with pytest.raises(ValueError):
        clean_overrides({"watermark": {"nested": True}})


async def test_n_greater_than_one_is_requested_one_by_one() -> None:
    seen, handler = _recording(
        [httpx.Response(200, json={"data": [{"url": "https://cdn.example.com/x.png"}]})]
    )
    result = await _provider(handler).generate_image(ImageRequest(prompt="p", n=3))
    assert len(seen) == 3
    assert all("n" not in json.loads(r.content) for r in seen)
    assert len(result.urls) == 3


# ------------------------------------------------------------------ 适配器：两种返回


async def test_b64_json_and_url_are_both_collected_in_order() -> None:
    png_b64 = base64.b64encode(PNG).decode()
    jpeg_b64 = "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()
    _, handler = _recording(
        [
            httpx.Response(
                200,
                json={
                    "data": [
                        {"b64_json": png_b64, "revised_prompt": "改写后的 A"},
                        {"url": "https://cdn.example.com/b.png", "revised_prompt": "改写后的 B"},
                        {"b64_json": jpeg_b64},
                    ]
                },
            )
        ]
    )
    result = await _provider(handler).generate_image(ImageRequest(prompt="p"))
    assert result.urls == ["https://cdn.example.com/b.png"]
    assert result.inline == [PNG, JPEG]
    # 与 Worker 的落库顺序一致：先 URL 后内联字节
    assert result.actual_prompts == ["改写后的 B", "改写后的 A", ""]


@pytest.mark.parametrize(
    "payload",
    [
        {"data": []},
        {"data": [{"b64_json": ""}]},
        {"nope": 1},
        [],
    ],
)
async def test_empty_or_malformed_payload_is_unavailable(payload: Any) -> None:
    _, handler = _recording([httpx.Response(200, json=payload)])
    with pytest.raises(AppError) as exc:
        await _provider(handler).generate_image(ImageRequest(prompt="p"))
    assert exc.value.code == "provider.unavailable"


async def test_invalid_base64_is_rejected() -> None:
    _, handler = _recording([httpx.Response(200, json={"data": [{"b64_json": "@@not-b64@@"}]})])
    with pytest.raises(AppError) as exc:
        await _provider(handler).generate_image(ImageRequest(prompt="p"))
    assert exc.value.code == "provider.unavailable"


# ------------------------------------------------------------------ 适配器：错误映射


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (401, {"error": {"message": "invalid api key"}}, "provider.account.insufficient"),
        (403, {"error": {"message": "forbidden"}}, "provider.account.insufficient"),
        (429, {"error": {"message": "slow down"}}, "provider.rate_limit.exceeded"),
        (
            400,
            {
                "error": {
                    "code": "1301",
                    "message": "系统检测到输入或生成内容可能包含不安全或敏感内容",
                }
            },
            "provider.content.rejected",
        ),
        (
            400,
            {"error": {"code": "InputTextSensitiveContentDetected", "message": "sensitive"}},
            "provider.content.rejected",
        ),
        (400, {"error": {"message": "size not supported"}}, "provider.params.invalid"),
        (500, {"error": {"message": "boom"}}, "provider.unavailable"),
    ],
)
async def test_status_codes_map_to_error_catalog(status: int, body: Any, code: str) -> None:
    _, handler = _recording([httpx.Response(status, json=body)])
    with pytest.raises(AppError) as exc:
        await _provider(handler).generate_image(ImageRequest(prompt="p"))
    assert exc.value.code == code
    assert KEY not in exc.value.message


async def test_timeout_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(AppError) as exc:
        await _provider(handler).generate_image(ImageRequest(prompt="p"))
    assert exc.value.code == "provider.transient.timeout"


# ------------------------------------------------------------------ 适配器：出网


async def test_redirect_on_generation_is_refused() -> None:
    seen, handler = _recording(
        [httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})]
    )
    with pytest.raises(AppError):
        await _provider(handler).generate_image(ImageRequest(prompt="p"))
    assert len(seen) == 1, "跳转不能被跟随"


@pytest.mark.parametrize("address", ["10.0.0.7", "127.0.0.1", "169.254.169.254", "::1"])
async def test_dns_resolving_to_private_is_refused_before_request(address: str) -> None:
    async def _resolve(host: str) -> list[str]:
        del host
        return ["93.184.216.34", address]

    endpoint_url.set_resolver(_resolve)
    seen, handler = _recording([httpx.Response(200, json={"data": [{"url": "https://x.y/z"}]})])
    provider = _provider(handler)
    with pytest.raises(AppError) as exc:
        await provider.generate_image(ImageRequest(prompt="p"))
    assert exc.value.code == "provider.params.invalid"
    with pytest.raises(AppError):
        await provider.verify_key()
    assert seen == [], "解析到内网时一个请求都不该发出去"


@pytest.mark.parametrize(
    "base_url",
    ["http://img.example.com/v1", "https://192.168.0.2/v1", "https://u:p@img.example.com/v1"],
)
def test_constructor_refuses_unsafe_base_url(base_url: str) -> None:
    with pytest.raises(AppError):
        _provider(lambda r: httpx.Response(200), base_url=base_url)


async def test_verify_key_lists_models_and_tolerates_404() -> None:
    seen, handler = _recording([httpx.Response(200, json={"data": []})])
    assert await _provider(handler).verify_key() == "鉴权通过"
    (req,) = seen
    assert (req.method, str(req.url)) == ("GET", f"{BASE}/models")

    _, handler = _recording([httpx.Response(404, text="not found")])
    assert "地址可达" in await _provider(handler).verify_key()

    _, handler = _recording([httpx.Response(401, text="bad key")])
    with pytest.raises(AppError) as exc:
        await _provider(handler).verify_key()
    assert exc.value.code == "provider.account.insufficient"


# ------------------------------------------------------------------ 结果下载：形状校验


@pytest.mark.parametrize(
    "url",
    [
        "https://cdn.example.com/a.png",
        "https://bucket.oss.example.com/a.png?Expires=1&Signature=abc%2F",  # 签名链接靠 query
        "https://1.1.1.1/a.png",
    ],
)
def test_download_url_accepts_public_https(url: str) -> None:
    assert endpoint_url.check_download_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://cdn.example.com/a.png",
        "ftp://cdn.example.com/a.png",
        "file:///etc/passwd",
        "https://user:pw@cdn.example.com/a.png",
        "https://localhost/a.png",
        "https://x.localhost/a.png",
        "https://127.0.0.1/a.png",
        "https://10.1.2.3/a.png",
        "https://192.168.1.1/a.png",
        "https://169.254.169.254/latest/meta-data/iam",
        "https://[::1]/a.png",
        "https://[fd00::1]/a.png",
        "https://0.0.0.0/a.png",
        "https://metadata.google.internal/a.png",
        "https://cdn.example.com/a b.png",
    ],
)
def test_download_url_rejects_unsafe(url: str) -> None:
    with pytest.raises(AppError) as exc:
        endpoint_url.check_download_url(url)
    assert exc.value.code == "provider.params.invalid"


def test_download_url_drops_fragment() -> None:
    assert (
        endpoint_url.check_download_url("https://cdn.example.com/a.png?x=1#frag")
        == "https://cdn.example.com/a.png?x=1"
    )


# ------------------------------------------------------------------ 结果下载：Worker


class _Seen(list[httpx.Request]):
    """Worker 下载发出去的请求；`routes` 是按 URL 定制的响应，没写的回一张 PNG。"""

    def __init__(self) -> None:
        super().__init__()
        self.routes: dict[str, httpx.Response] = {}


@pytest.fixture
def mock_http(monkeypatch: pytest.MonkeyPatch) -> _Seen:
    """把 Worker 下载用的 `httpx.AsyncClient` 换成走 MockTransport 的同一个类。"""
    seen = _Seen()
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return seen.routes.get(str(request.url), httpx.Response(200, content=PNG))

    def factory(**kw: Any) -> httpx.AsyncClient:
        assert kw.get("follow_redirects") is False, "下载必须显式不跟随跳转"
        return real(**kw, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(generation.httpx, "AsyncClient", factory)
    return seen


async def test_user_controlled_download_refuses_redirect(mock_http: _Seen) -> None:
    url = "https://cdn.example.com/a.png"
    mock_http.routes[url] = httpx.Response(302, headers={"location": "https://10.0.0.1/x"})
    with pytest.raises(AppError) as exc:
        await generation._download(url, user_controlled=True)
    assert exc.value.code == "provider.params.invalid"
    assert [str(r.url) for r in mock_http] == [url], "跳转目标不能被请求"


async def test_user_controlled_download_refuses_private_resolution(mock_http: _Seen) -> None:
    async def _resolve(host: str) -> list[str]:
        del host
        return ["169.254.169.254"]

    endpoint_url.set_resolver(_resolve)
    with pytest.raises(AppError) as exc:
        await generation._download("https://cdn.example.com/a.png", user_controlled=True)
    assert exc.value.code == "provider.params.invalid"
    assert list(mock_http) == []


@pytest.mark.parametrize(
    "url", ["http://cdn.example.com/a.png", "https://127.0.0.1/a.png", "https://u:p@x.com/a"]
)
async def test_user_controlled_download_refuses_bad_shape(mock_http: _Seen, url: str) -> None:
    with pytest.raises(AppError):
        await generation._download(url, user_controlled=True)
    assert list(mock_http) == []


async def test_user_controlled_download_ok(mock_http: _Seen) -> None:
    data = await generation._download("https://cdn.example.com/a.png?sig=1", user_controlled=True)
    assert data == PNG


def test_user_controlled_bytes_must_be_an_image() -> None:
    assert generation._image_mime(PNG, user_controlled=True) == "image/png"
    assert generation._image_mime(JPEG, user_controlled=True) == "image/jpeg"
    with pytest.raises(AppError):
        generation._image_mime(b"<html>not an image</html>", user_controlled=True)
    # 平台路由（万相）维持原行为
    assert generation._image_mime(b"whatever", user_controlled=False) == "image/png"


def test_only_org_routes_are_user_controlled() -> None:
    assert catalog.is_org_provider("provider.org:00000000-0000-0000-0000-000000000001")
    assert not catalog.is_org_provider("provider.dashscope")
    assert not catalog.is_org_provider(None)


# ------------------------------------------------------------------ 预设


def test_shipped_presets_load_and_follow_the_whitelist() -> None:
    presets.presets.cache_clear()
    loaded = presets.presets()
    ids = [p.preset_id for p in loaded]
    assert len(ids) == len(set(ids)) >= 10
    for preset in loaded:
        assert preset.protocols, preset.preset_id
        assert all(catalog.protocol_spec(p) is not None for p in preset.protocols)
        assert all(m.protocol in preset.protocols for m in preset.models)
        if preset.base_url:
            assert endpoint_url.normalize_base_url(preset.base_url) == preset.base_url
    by_id = {p.preset_id: p for p in loaded}
    assert by_id["volcengine_seedream"].request_overrides == {"watermark": False}
    assert by_id["openai_images_relay"].protocols == ("openai_images",)
    assert presets.get("no_such") is None


_GOOD = """
presets:
  - preset_id: demo
    label: Demo
    base_url: https://api.example.com/v1
    docs_url: https://example.com/docs
    key_url: null
    icon: demo
    models:
      - {model_id: m1, protocol: openai_chat}
"""


def test_minimal_preset_parses() -> None:
    (preset,) = presets.parse(_GOOD)
    assert preset.protocols == ("openai_chat",)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("    icon: demo\n", "    icon: demo\n    price_per_call: 0.1\n"),  # 价格类字段
        ("    icon: demo\n", "    icon: demo\n    unknown: 1\n"),  # 未知字段
        ("protocol: openai_chat", "protocol: kling_image"),  # 白名单外协议
        ("https://api.example.com/v1", "http://api.example.com/v1"),
        ("https://api.example.com/v1", "https://10.0.0.1/v1"),
        ("https://api.example.com/v1", "https://169.254.169.254/v1"),
        ("https://example.com/docs", "http://example.com/docs"),
        ("    icon: demo\n", "    icon: demo\n    request_overrides: {watermark: false}\n"),
        ("preset_id: demo", "preset_id: Demo Vendor"),
        ("model_id: m1", "model_id: 'has space'"),
    ],
)
def test_bad_presets_are_rejected(old: str, new: str) -> None:
    assert old in _GOOD
    with pytest.raises(ValueError):
        presets.parse(_GOOD.replace(old, new))


def test_duplicate_preset_ids_and_bad_overrides_are_rejected() -> None:
    dup = _GOOD + _GOOD.split("presets:\n", 1)[1]
    with pytest.raises(ValueError, match="重复"):
        presets.parse(dup)
    images = _GOOD.replace("protocol: openai_chat", "protocol: openai_images")
    ok = images.replace(
        "    icon: demo\n", "    icon: demo\n    request_overrides: {watermark: false}\n"
    )
    assert presets.parse(ok)[0].request_overrides == {"watermark": False}
    bad = images.replace(
        "    icon: demo\n", "    icon: demo\n    request_overrides: {response_format: url}\n"
    )
    with pytest.raises(ValueError):
        presets.parse(bad)
    with pytest.raises(ValueError):
        presets.parse("- just a list")
    empty = _GOOD.replace("    models:\n      - {model_id: m1, protocol: openai_chat}\n", "")
    with pytest.raises(ValueError):
        presets.parse(empty)  # 没有模型也没写 protocols
