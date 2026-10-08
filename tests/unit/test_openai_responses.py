"""OpenAI Responses 文本协议 `openai_responses`（USER_FLOW_REPAIR responses，ADR-039 后续决定）。

真实案例：cchost.ai + `gpt-6.1-sol`，`GET /v1/models` 鉴权通过，`POST /v1/chat/completions`
回 404；CC Switch 里这条供应商的上游格式是 OpenAI Responses。这里钉死：

1. `TextRequest` → Responses 请求体的映射（instructions / input / max_output_tokens /
   text.format），**不发** temperature 与 Chat 专有字段；
2. 结果解析：顶层 `output_text`、`output[].content[].text`、用量三个字段；
   空内容、拒答、`incomplete`、`failed` 都报错，不把截断的东西当成功；
3. 与 `openai_chat` 共用的出网校验、不跟随跳转、Key 来源日志、错误分层与脱敏都还在；
4. 测试连接仍只读 `GET /models`，结论写明 `POST /responses` 未试调用；
5. 协议只绑定文本能力，不会被当成出图；旧 `openai_chat` 行为不变。

零网络：解析函数与 HTTP 传输全部替换，不访问任何真实 Provider。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest

from adapters.providers import endpoint_url, openai_compat
from adapters.providers.base import KeySource, TextRequest
from adapters.providers.model_discovery import normalize_api_base
from adapters.providers.openai_compat import OpenAICompatTextProvider
from adapters.providers.openai_responses import OpenAIResponsesTextProvider
from apps.api.core.errors import AppError
from apps.api.modules.gateway import breaker, catalog, probe, upstreams
from apps.api.modules.gateway import service as gw

TEXT = "text_generation"
CONN_ID = uuid.UUID("bbbbbbbb-cccc-dddd-eeee-ffffffffffff")
CONN = catalog.org_provider_id(CONN_ID)
KEY = "sk-relay-rsp-key-9f8e7d6c5b4a3210"
MODEL = "gpt-6.1-sol"
BASE = "https://relay.example/v1"
CONNECTION = upstreams.ResolvedConnection(
    connection_id=CONN_ID,
    label="relay.example",
    preset_id=None,
    base_url=BASE,
    models=((MODEL, "openai_responses"),),
    enabled=True,
    api_key=KEY,
)

Handler = Callable[[httpx.Request], httpx.Response]


def _ok_body(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "resp_1",
        "object": "response",
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "output": [
            {"type": "reasoning", "id": "rs_1", "summary": []},
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": '{"a":', "annotations": []},
                    {"type": "output_text", "text": " 1}", "annotations": []},
                ],
            },
        ],
        "usage": {
            "input_tokens": 120,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 45,
            "output_tokens_details": {"reasoning_tokens": 30},
            "total_tokens": 165,
        },
    }
    body.update(over)
    return body


@pytest.fixture(autouse=True)
def _public_dns() -> Iterator[None]:
    async def _resolve(host: str) -> list[str]:
        del host
        return ["93.184.216.34"]

    endpoint_url.set_resolver(_resolve)
    yield
    endpoint_url.set_resolver(None)


def _recorder(status: int, body: Any) -> tuple[Handler, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET" and request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "other"}, {"id": MODEL}]})
        if isinstance(body, (dict, list)):
            return httpx.Response(status, json=body)
        return httpx.Response(status, text=str(body))

    return handler, seen


def _adapter(handler: Handler, *, base_url: str = BASE) -> OpenAIResponsesTextProvider:
    return OpenAIResponsesTextProvider(
        base_url=base_url, api_key=KEY, model_id=MODEL, transport=httpx.MockTransport(handler)
    )


# ------------------------------------------------------------------ 协议白名单


def test_protocol_is_text_only_and_never_an_image_capability() -> None:
    spec = catalog.protocol_spec("openai_responses")
    assert spec is not None
    assert spec.capability == TEXT
    assert spec.adapter is OpenAIResponsesTextProvider
    assert spec.consistency_verified is None  # 文本协议不谈出图一致性
    assert "openai_responses" in catalog.protocols_for(TEXT)
    assert "openai_responses" not in catalog.protocols_for("image_generation")
    assert "openai_responses" in catalog.OPENAI_BASE_PROTOCOLS
    # 旧协议绑定不变
    chat = catalog.protocol_spec("openai_chat")
    assert chat is not None and chat.adapter is OpenAICompatTextProvider


def test_build_adapter_picks_the_adapter_by_protocol() -> None:
    responses = upstreams.build_adapter(CONNECTION, model_id=MODEL, protocol="openai_responses")
    chat = upstreams.build_adapter(CONNECTION, model_id=MODEL, protocol="openai_chat")
    assert type(responses) is OpenAIResponsesTextProvider
    assert type(chat) is OpenAICompatTextProvider
    assert responses.key_source is KeySource.ORG and responses.provider_id == CONN


def test_pick_model_routes_a_responses_model_for_text_but_not_for_image() -> None:
    kw: dict[str, Any] = {
        "label": "relay",
        "models": ((MODEL, "openai_responses"),),
        "enabled": True,
        "connection_id": CONN_ID,
    }
    assert upstreams.pick_model(TEXT, model_id=MODEL, **kw) == (MODEL, "openai_responses")
    with pytest.raises(AppError) as exc:
        upstreams.pick_model("image_generation", model_id=None, **kw)
    assert exc.value.detail["reason"] == "capability_mismatch"


# ------------------------------------------------------------------ 请求体映射


def test_request_body_follows_the_responses_contract() -> None:
    adapter = _adapter(_recorder(200, {})[0])
    body = adapter.request_body(
        TextRequest(system="你是编剧，输出 json", user="写一段", max_output_tokens=4096)
    )
    assert body == {
        "model": MODEL,
        "instructions": "你是编剧，输出 json",
        "input": "写一段",
        "max_output_tokens": 4096,
        "text": {"format": {"type": "json_object"}},
    }
    # Chat 专有字段与 Codex 系拒收的采样参数一律不发
    for absent in ("messages", "max_tokens", "response_format", "temperature", "stream"):
        assert absent not in body


def test_request_body_without_json_mode_or_system_and_with_tiny_budget() -> None:
    adapter = _adapter(_recorder(200, {})[0])
    body = adapter.request_body(
        TextRequest(system="", user="hi", json_mode=False, max_output_tokens=1, temperature=0.2)
    )
    assert body == {"model": MODEL, "input": "hi", "max_output_tokens": 16}


def test_chat_request_body_is_unchanged() -> None:
    chat = OpenAICompatTextProvider(base_url=BASE, api_key=KEY, model_id="m")
    body = chat.request_body(TextRequest(system="s", user="u", max_output_tokens=100))
    assert body == {
        "model": "m",
        "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        "max_tokens": 100,
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
    }


# ------------------------------------------------------------------ 正式调用与解析


async def test_generate_posts_once_to_responses_and_parses_output_and_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[dict[str, Any]] = []
    monkeypatch.setattr(openai_compat, "signal_key_source", lambda **kw: signals.append(kw))
    handler, seen = _recorder(200, _ok_body())

    result = await _adapter(handler).generate_text(TextRequest(system="s json", user="u"))

    assert [f"{r.method} {r.url}" for r in seen] == [f"POST {BASE}/responses"]
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"
    sent = json.loads(seen[0].content)
    assert sent["text"] == {"format": {"type": "json_object"}} and "temperature" not in sent
    assert result.text == '{"a": 1}'  # 推理项不算正文，多段 output_text 按序拼接
    assert (result.tokens_in, result.tokens_out, result.reasoning_tokens) == (120, 45, 30)
    assert result.model_id == MODEL
    # Key 来源日志在真正发请求那一层：用的是组织自己的 Key
    assert signals == [
        {"provider_id": "provider.org", "model_id": MODEL, "key_source": KeySource.ORG}
    ]


async def test_missing_status_still_reads_the_output() -> None:
    body = _ok_body()
    del body["status"]
    handler, _ = _recorder(200, body)
    result = await _adapter(handler).generate_text(TextRequest(system="s", user="u"))
    assert result.text == '{"a": 1}'


async def test_in_progress_with_output_reports_the_status() -> None:
    handler, _ = _recorder(200, _ok_body(status="in_progress"))
    with pytest.raises(AppError) as exc:
        await _adapter(handler).generate_text(TextRequest(system="s", user="u"))
    assert exc.value.detail["response_status"] == "in_progress"
    assert "status=in_progress" in exc.value.message


async def test_top_level_output_text_is_preferred() -> None:
    handler, _ = _recorder(200, _ok_body(output_text='{"b": 2}'))
    result = await _adapter(handler).generate_text(TextRequest(system="s", user="u"))
    assert result.text == '{"b": 2}'


async def test_chat_style_usage_names_are_accepted_and_missing_usage_is_zero() -> None:
    usage = {
        "prompt_tokens": 7,
        "completion_tokens": 9,
        "completion_tokens_details": {"reasoning_tokens": 2},
    }
    handler, _ = _recorder(200, _ok_body(usage=usage))
    result = await _adapter(handler).generate_text(TextRequest(system="s", user="u"))
    assert (result.tokens_in, result.tokens_out, result.reasoning_tokens) == (7, 9, 2)

    handler, _ = _recorder(200, _ok_body(usage=None))
    result = await _adapter(handler).generate_text(TextRequest(system="s", user="u"))
    assert (result.tokens_in, result.tokens_out, result.reasoning_tokens) == (0, 0, 0)


@pytest.mark.parametrize(
    ("body", "code"),
    [
        # 只有推理、没有正文：推理吃满预算的典型表现
        (_ok_body(output=[{"type": "reasoning", "summary": []}]), "provider.transient.timeout"),
        (_ok_body(output=[]), "provider.transient.timeout"),
        (
            _ok_body(
                output=[{"type": "message", "content": [{"type": "output_text", "text": "  "}]}]
            ),
            "provider.transient.timeout",
        ),
        (
            _ok_body(
                output=[{"type": "message", "content": [{"type": "refusal", "refusal": "不能"}]}]
            ),
            "provider.content.rejected",
        ),
        (
            _ok_body(status="incomplete", incomplete_details={"reason": "max_output_tokens"}),
            "provider.transient.timeout",
        ),
        (
            _ok_body(status="incomplete", incomplete_details={"reason": "content_filter"}),
            "provider.content.rejected",
        ),
        (
            _ok_body(status="failed", error={"code": "server_error", "message": "boom"}),
            "provider.unavailable",
        ),
        (_ok_body(status="cancelled"), "provider.unavailable"),
        # 非终态即使带了正文也不算成功
        (_ok_body(status="queued"), "provider.unavailable"),
        (_ok_body(status="in_progress"), "provider.unavailable"),
        (_ok_body(status="something_new"), "provider.unavailable"),
    ],
)
async def test_empty_or_unfinished_responses_are_errors(body: dict[str, Any], code: str) -> None:
    handler, _ = _recorder(200, body)
    with pytest.raises(AppError) as exc:
        await _adapter(handler).generate_text(TextRequest(system="s", user="u"))
    assert exc.value.code == code
    assert exc.value.detail["model_id"] == MODEL


async def test_failed_status_carries_only_a_well_formed_upstream_code() -> None:
    handler, _ = _recorder(200, _ok_body(status="failed", error={"code": "server_error"}))
    with pytest.raises(AppError) as exc:
        await _adapter(handler).generate_text(TextRequest(system="s", user="u"))
    assert exc.value.detail["upstream_error_code"] == "server_error"

    handler, _ = _recorder(200, _ok_body(status="failed", error={"code": f"bad {KEY}"}))
    with pytest.raises(AppError) as exc:
        await _adapter(handler).generate_text(TextRequest(system="s", user="u"))
    assert "upstream_error_code" not in exc.value.detail


async def test_non_json_body_is_an_error() -> None:
    handler, _ = _recorder(200, "<html>gateway</html>")
    with pytest.raises(AppError) as exc:
        await _adapter(handler).generate_text(TextRequest(system="s", user="u"))
    assert exc.value.code == "provider.unavailable"


# ------------------------------------------------------------------ 出网防护（继承自 openai_chat）


async def test_redirect_is_refused_not_followed() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://evil.example/v1/responses"})

    with pytest.raises(AppError) as exc:
        await _adapter(handler).generate_text(TextRequest(system="s", user="u"))
    assert exc.value.code == "provider.params.invalid"
    assert seen == [f"{BASE}/responses"]


async def test_private_resolution_is_refused_before_any_request() -> None:
    async def _private(host: str) -> list[str]:
        del host
        return ["10.0.0.8"]

    endpoint_url.set_resolver(_private)
    handler, seen = _recorder(200, _ok_body())
    with pytest.raises(AppError):
        await _adapter(handler).generate_text(TextRequest(system="s", user="u"))
    assert seen == []


# ------------------------------------------------------------------ 测试连接仍只读模型列表


async def test_verify_key_only_reads_models_and_names_the_responses_api() -> None:
    handler, seen = _recorder(500, {})
    message = await _adapter(handler).verify_key()
    assert [f"{r.method} {r.url}" for r in seen] == [f"GET {BASE}/models"]
    assert message.startswith("模型列表接口鉴权通过")
    assert "POST /responses" in message and "/chat/completions" not in message
    assert "以第一次生成为准" in message


async def test_chat_verify_message_still_names_chat_completions() -> None:
    handler, _ = _recorder(500, {})
    chat = OpenAICompatTextProvider(
        base_url=BASE, api_key=KEY, model_id=MODEL, transport=httpx.MockTransport(handler)
    )
    message = await chat.verify_key()
    assert "POST /chat/completions" in message and "以第一次生成为准" in message


@pytest.mark.parametrize(
    "raw", [f"{BASE}/responses", f"{BASE}/responses/", "https://relay.example/responses"]
)
def test_responses_operation_url_normalizes_to_the_base(raw: str) -> None:
    assert normalize_api_base(raw) == BASE


async def test_draft_test_with_responses_url_probes_the_normalized_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler, seen = _recorder(404, {})
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
        protocol="openai_responses",
        model_id=MODEL,
        base_url=f"{BASE}/responses",
        api_key=KEY,
    )

    assert [f"{r.method} {r.url}" for r in seen] == [f"GET {BASE}/models"]
    assert result.ok and "POST /responses" in result.message
    assert KEY not in result.message


# ------------------------------------------------------------------ 走 Gateway 全链路


@pytest.fixture
def org_route(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """组织默认指向一个 `openai_responses` 连接；适配器换上 Mock 传输（零网络）。"""
    state: dict[str, Any] = {"handler": None}

    async def _default(**_kw: Any) -> upstreams.DefaultRow:
        return upstreams.DefaultRow(provider_id=CONN, model_id=MODEL, key_source="org")

    async def _connection(
        *, org_id: uuid.UUID, connection_id: uuid.UUID
    ) -> upstreams.ResolvedConnection | None:
        del org_id
        return CONNECTION if connection_id == CONN_ID else None

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


async def test_gateway_generates_through_a_responses_connection(org_route: dict[str, Any]) -> None:
    handler, seen = _recorder(200, _ok_body())
    org_route["handler"] = handler
    org_id = uuid.uuid4()
    await breaker.reset(breaker.scope(CONN, org_id=org_id))

    result = await gw.generate_text(TextRequest(system="s", user="u"), org_id=org_id)

    assert [f"{r.method} {r.url}" for r in seen] == [f"POST {BASE}/responses"]
    assert result.text == '{"a": 1}' and result.tokens_out == 45
    await breaker.reset(breaker.scope(CONN, org_id=org_id))


async def test_gateway_404_names_the_responses_operation(org_route: dict[str, Any]) -> None:
    handler, seen = _recorder(404, {"error": {"message": "not found", "code": "not_found"}})
    org_route["handler"] = handler
    org_id = uuid.uuid4()
    await breaker.reset(breaker.scope(CONN, org_id=org_id))
    with pytest.raises(AppError) as exc:
        await gw.generate_text(TextRequest(system="s", user="u"), org_id=org_id)
    await breaker.reset(breaker.scope(CONN, org_id=org_id))

    assert len(seen) == 1  # 失败不回落平台、不换连接
    err = exc.value
    assert err.code == "provider.byok.rejected"
    assert err.detail["reason"] == "upstream_not_found"
    assert err.detail["operation"] == "POST /v1/responses"
    assert err.detail["model_id"] == MODEL


async def test_gateway_401_is_a_key_error_and_the_key_is_redacted(
    org_route: dict[str, Any],
) -> None:
    handler, _ = _recorder(
        401, {"error": {"message": f"Incorrect API key {KEY}", "code": "invalid_api_key"}}
    )
    org_route["handler"] = handler
    org_id = uuid.uuid4()
    await breaker.reset(breaker.scope(CONN, org_id=org_id))
    with pytest.raises(AppError) as exc:
        await gw.generate_text(TextRequest(system="s", user="u"), org_id=org_id)
    await breaker.reset(breaker.scope(CONN, org_id=org_id))

    err = exc.value
    assert err.detail["reason"] == "upstream_auth_rejected"
    assert KEY not in err.message and KEY not in json.dumps(err.detail, ensure_ascii=False)
    assert KEY[-8:] not in err.message
