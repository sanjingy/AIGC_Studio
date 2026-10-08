"""自带上游调用失败时，用户看到的原因要落在真正失败的那一层（USER_FLOW_REPAIR provider-real）。

真实事故：中转对 `GET /v1/models` 鉴权通过、列出了所选模型，测试连接因此显示通过；
正式 `POST /v1/chat/completions` 却回 404
`{"error": {"message": "not found", "code": "not_found"}}`，
Gateway 包成 `provider.byok.rejected`，界面说"你的 API Key 调用失败"，
用户去换了一把本来没问题的 Key。这里钉死三件事：

1. 测试连接的结论写明"只读了模型列表、生成接口未试调用"，不当作"正式生成已验证"；
2. 正式调用的 404 归到 `upstream_not_found`，带上接口路径、状态码、上游错误码与模型，
   只有 401 / 403 才归到 `upstream_auth_rejected`；
3. 保存的是完整请求地址时，测试与正式调用都打规整后的同一个 base。

零网络：解析函数与 HTTP 传输全部替换，不访问任何真实 Provider。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest

from adapters.providers import endpoint_url
from adapters.providers.base import TextRequest
from adapters.providers.model_discovery import normalize_api_base
from adapters.providers.openai_compat import OpenAICompatTextProvider
from apps.api.core.errors import AppError
from apps.api.modules.gateway import breaker, catalog, probe, upstreams
from apps.api.modules.gateway import service as gw

TEXT = "text_generation"
CONN_ID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
CONN = catalog.org_provider_id(CONN_ID)
KEY = "sk-relay-user-key-0123456789abcdef"
MODEL = "gpt-6.1-sol"
CONNECTION = upstreams.ResolvedConnection(
    connection_id=CONN_ID,
    label="relay.example",
    preset_id=None,
    base_url="https://relay.example/v1",
    models=((MODEL, "openai_chat"),),
    enabled=True,
    api_key=KEY,
)

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(autouse=True)
def _public_dns() -> Iterator[None]:
    async def _resolve(host: str) -> list[str]:
        del host
        return ["93.184.216.34"]

    endpoint_url.set_resolver(_resolve)
    yield
    endpoint_url.set_resolver(None)


def _relay(post_status: int, post_body: dict[str, Any]) -> tuple[Handler, list[str]]:
    """模型列表正常、生成接口按给定状态回的中转。"""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url}")
        if request.method == "GET" and request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "other"}, {"id": MODEL}]})
        return httpx.Response(post_status, json=post_body)

    return handler, seen


@pytest.fixture
def org_route(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """组织默认指向一个已保存连接；连接的适配器换上 Mock 传输（零网络）。"""
    state: dict[str, Any] = {"handler": None, "connection": CONNECTION}

    async def _default(**_kw: Any) -> upstreams.DefaultRow:
        return upstreams.DefaultRow(provider_id=CONN, model_id=MODEL, key_source="org")

    async def _connection(
        *, org_id: uuid.UUID, connection_id: uuid.UUID
    ) -> upstreams.ResolvedConnection | None:
        del org_id
        conn: upstreams.ResolvedConnection = state["connection"]
        return conn if conn.connection_id == connection_id else None

    async def _none(**_kw: Any) -> None:
        return None

    real_build = upstreams.build_adapter

    def _build(connection: upstreams.ResolvedConnection, **kw: Any) -> Any:
        adapter = real_build(connection, **kw)
        adapter._transport = httpx.MockTransport(state["handler"])
        return adapter

    monkeypatch.setattr(gw, "_load_org_default", _default)
    monkeypatch.setattr(gw, "_load_org_connection", _connection)
    monkeypatch.setattr(gw, "_load_org_key", _none)
    monkeypatch.setattr(gw, "_load_model_preference", _none)
    monkeypatch.setattr(upstreams, "build_adapter", _build)
    gw.reset_registry()
    yield state
    gw.reset_registry()


async def _generate(org_id: uuid.UUID) -> AppError:
    await breaker.reset(breaker.scope(CONN, org_id=org_id))
    with pytest.raises(AppError) as exc:
        await gw.generate_text(TextRequest(system="s", user="u"), org_id=org_id)
    await breaker.reset(breaker.scope(CONN, org_id=org_id))
    return exc.value


# ------------------------------------------------------------------ 测试连接不等于生成已验证


async def test_model_list_success_is_not_reported_as_generation_verified() -> None:
    handler, seen = _relay(404, {"error": {"message": "not found", "code": "not_found"}})
    adapter = OpenAICompatTextProvider(
        base_url=CONNECTION.base_url,
        api_key=KEY,
        model_id=MODEL,
        transport=httpx.MockTransport(handler),
    )

    message = await adapter.verify_key()

    assert seen == ["GET https://relay.example/v1/models"]  # 没有发生成请求
    assert f"里有 {MODEL}" in message
    assert "没有试调用生成接口" in message
    assert "POST /chat/completions" in message
    # 前端 `classifyTest` 靠"以第一次"把它画成中性，不画成"连接正常"
    assert "以第一次生成为准" in message
    # 只能说模型列表接口鉴权通过；生成接口的鉴权与计费由供应商决定，不作保证
    assert message.startswith("模型列表接口鉴权通过")
    assert "不计费" not in message


# ------------------------------------------------------------------ 正式调用的失败分层


async def test_generation_404_after_models_ok_is_not_called_a_key_error(
    org_route: dict[str, Any],
) -> None:
    handler, seen = _relay(404, {"error": {"message": "not found", "code": "not_found"}})
    org_route["handler"] = handler

    err = await _generate(uuid.uuid4())

    assert seen == ["POST https://relay.example/v1/chat/completions"]
    assert err.code == "provider.byok.rejected"
    assert err.detail["reason"] == "upstream_not_found"
    assert err.detail["upstream_code"] == "provider.unavailable"
    assert err.detail["http_status"] == 404
    assert err.detail["operation"] == "POST /v1/chat/completions"
    assert err.detail["upstream_error_code"] == "not_found"
    assert err.detail["model_id"] == MODEL
    assert err.detail["provider_id"] == CONN
    assert "API Key" not in err.spec.user_message


async def test_generation_404_with_model_not_found_code_blames_the_model(
    org_route: dict[str, Any],
) -> None:
    handler, _ = _relay(
        404, {"error": {"message": "The model does not exist", "code": "model_not_found"}}
    )
    org_route["handler"] = handler

    err = await _generate(uuid.uuid4())

    assert err.detail["reason"] == "upstream_model_not_found"


@pytest.mark.parametrize("status", [401, 403])
async def test_generation_401_403_is_a_key_error_and_key_is_redacted(
    org_route: dict[str, Any], status: int
) -> None:
    handler, _ = _relay(status, {"error": {"message": f"invalid key {KEY}", "code": "bad_key"}})
    org_route["handler"] = handler

    err = await _generate(uuid.uuid4())

    assert err.detail["reason"] == "upstream_auth_rejected"
    assert err.detail["upstream_code"] == "provider.account.insufficient"
    assert err.detail["http_status"] == status
    assert KEY not in err.message
    assert KEY[4:16] not in err.message
    assert all(KEY not in str(v) for v in err.detail.values())


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (402, "upstream_quota_exhausted"),
        (429, "upstream_rate_limited"),
        (400, "upstream_request_rejected"),
        (502, "upstream_unavailable"),
        (302, "upstream_redirect"),
    ],
)
async def test_other_statuses_get_their_own_reason(
    org_route: dict[str, Any], status: int, reason: str
) -> None:
    handler, _ = _relay(status, {"error": {"message": "x"}})
    org_route["handler"] = handler

    err = await _generate(uuid.uuid4())

    assert err.code == "provider.byok.rejected"
    assert err.detail["reason"] == reason


async def test_upstream_error_code_with_key_material_is_not_echoed() -> None:
    """`error.code` 只收短码形状；就算形状合法也要过脱敏。"""
    resolution = gw.Resolution(capability=TEXT, routes=[], key_source=gw.KeySource.ORG, secret=KEY)
    exc = AppError(
        "provider.unavailable",
        message="HTTP 404",
        detail={"http_status": 404, "upstream_error_code": KEY[3:30]},
    )

    wrapped = gw._as_byok_error(exc, resolution)

    assert KEY[3:30] not in str(wrapped.detail)


# ------------------------------------------------------------------ 地址规整


@pytest.mark.parametrize(
    "saved",
    [
        "https://relay.example/v1/chat/completions",
        "https://relay.example/v1/responses",
        "https://relay.example/v1/models/",
        "https://relay.example",
    ],
)
def test_full_operation_url_normalizes_to_the_same_base(saved: str) -> None:
    assert normalize_api_base(saved) == "https://relay.example/v1"


async def test_draft_test_with_full_operation_url_probes_the_normalized_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """测试连接粘的是完整请求地址：要打 `{base}/models`，不能打成 `.../chat/completions/models`。"""
    handler, seen = _relay(404, {})
    real_build = upstreams.build_adapter

    def _build(connection: upstreams.ResolvedConnection, **kw: Any) -> Any:
        adapter = real_build(connection, **kw)
        adapter._transport = httpx.MockTransport(handler)
        return adapter

    monkeypatch.setattr(upstreams, "build_adapter", _build)
    monkeypatch.setattr(probe, "_prober", probe.LiveProber())  # 只在本用例：传输已是 Mock

    result = await upstreams.test_connection(
        None,  # type: ignore[arg-type]  # 草稿测试不读库
        org_id=uuid.uuid4(),
        protocol="openai_chat",
        model_id=MODEL,
        base_url="https://relay.example/v1/chat/completions",
        api_key=KEY,
    )

    assert seen == ["GET https://relay.example/v1/models"]
    assert result.ok
    assert "以第一次生成为准" in result.message
