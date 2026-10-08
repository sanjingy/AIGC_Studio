from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from adapters.providers import model_discovery as discovery
from apps.api.core.errors import AppError


@pytest.fixture
def upstream(monkeypatch):
    calls = []
    real_client = httpx.AsyncClient

    def install(body, status=200, headers=None):
        def respond(request):
            calls.append(request)
            return httpx.Response(status, json=body, headers=headers)

        async def public(base):
            return None

        monkeypatch.setattr(discovery.endpoint_url, "assert_public_host", public)
        monkeypatch.setattr(
            discovery.httpx,
            "AsyncClient",
            lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
        )
        return calls

    return install


async def test_custom_models_from_full_request_url(upstream):
    calls = upstream(
        {"data": [{"id": "custom-text"}, {"id": "custom-text"}, {"id": "custom-image"}]}
    )
    models = await discovery.discover_models(
        base_url="https://gateway.example/api/v4/chat/completions", api_key="secret-test-key"
    )
    assert models == ["custom-text", "custom-image"]
    assert str(calls[0].url) == "https://gateway.example/api/v4/models"
    assert calls[0].method == "GET"
    assert calls[0].headers["Authorization"] == "Bearer secret-test-key"


async def test_model_response_cannot_echo_credentials(upstream):
    upstream(
        {
            "data": [
                {"id": "secret-test-key"},
                {"id": "secret-test"},
                {"id": "valid"},
                {"id": "bad id"},
                {},
            ]
        }
    )
    assert await discovery.discover_models(
        base_url="https://gateway.example", api_key="secret-test-key"
    ) == ["valid"]


@pytest.mark.parametrize("status", [401, 403, 404, 500, 302])
async def test_failure_does_not_follow_redirect_or_echo_upstream(upstream, status):
    calls = upstream({"error": "secret-test-key"}, status, {"Location": "https://127.0.0.1/models"})
    with pytest.raises(AppError) as caught:
        await discovery.discover_models(
            base_url="https://gateway.example", api_key="secret-test-key"
        )
    assert "secret-test-key" not in caught.value.message
    assert len(calls) == 1


@pytest.mark.parametrize("payload", [{}, {"data": "invalid"}, {"data": []}])
async def test_invalid_response_is_actionable(upstream, payload):
    upstream(payload)
    with pytest.raises(AppError):
        await discovery.discover_models(
            base_url="https://gateway.example", api_key="secret-test-key"
        )


async def test_private_host_is_rejected_before_network(monkeypatch):
    def forbidden_client(**kwargs):
        pytest.fail("private address must not reach HTTP client")

    monkeypatch.setattr(discovery.httpx, "AsyncClient", forbidden_client)
    with pytest.raises(AppError):
        await discovery.discover_models(base_url="https://127.0.0.1/v1", api_key="secret-test-key")


async def test_timeout_is_actionable(monkeypatch):
    real_client = httpx.AsyncClient

    async def public(base):
        return None

    def timeout(request):
        raise httpx.ReadTimeout("secret-test-key", request=request)

    monkeypatch.setattr(discovery.endpoint_url, "assert_public_host", public)
    monkeypatch.setattr(
        discovery.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(timeout), **kwargs),
    )
    with pytest.raises(AppError) as caught:
        await discovery.discover_models(
            base_url="https://gateway.example/v1", api_key="secret-test-key"
        )
    assert "secret-test-key" not in caught.value.message


async def test_oversized_response_is_rejected(upstream):
    upstream({"data": [{"id": "x" * discovery.MAX_RESPONSE_BYTES}]})
    with pytest.raises(AppError, match="响应过大"):
        await discovery.discover_models(
            base_url="https://gateway.example/v1", api_key="secret-test-key"
        )


async def test_saved_discovery_uses_org_scope_and_never_reads_key_before_lookup(monkeypatch):
    from apps.api.modules.gateway import upstreams

    org_id, connection_id = uuid4(), uuid4()
    expected_scope = (org_id, connection_id)

    async def missing(db, *, org_id, connection_id):
        assert (org_id, connection_id) == expected_scope
        raise AppError("common.not_found")

    def forbidden_decrypt(value):
        pytest.fail("must not decrypt a key for an unavailable connection")

    monkeypatch.setattr(upstreams, "_require_connection", missing)
    monkeypatch.setattr(upstreams, "decrypt_secret", forbidden_decrypt)
    with pytest.raises(AppError) as caught:
        await upstreams.discover_connection_models(
            None,
            org_id=org_id,
            connection_id=connection_id,
            base_url="https://gateway.example/v1",
            api_key=None,
        )
    assert caught.value.code == "common.not_found"


async def test_test_environment_discovery_is_explicit_mock_without_network(monkeypatch):
    from apps.api.modules.gateway import upstreams

    def forbidden_client(**kwargs):
        pytest.fail("ENV=test must never call an upstream")

    monkeypatch.setattr(discovery.httpx, "AsyncClient", forbidden_client)
    monkeypatch.setattr(upstreams, "get_settings", lambda: SimpleNamespace(env="test"))
    result = await upstreams.discover_connection_models(
        None,
        org_id=uuid4(),
        base_url="https://gateway.example/v1/chat/completions",
        api_key="secret-test-key",
    )
    assert result.mock is True
    assert result.base_url == "https://gateway.example/v1"
    assert "secret-test-key" not in result.model_dump_json()


@pytest.mark.parametrize(
    "address, expected",
    [
        ("https://gateway.example", "https://gateway.example/v1"),
        ("https://gateway.example/chat/completions", "https://gateway.example/v1"),
        ("https://gateway.example/v4/images/generations/", "https://gateway.example/v4"),
    ],
)
def test_address_normalization_respects_full_operation_path(address, expected):
    assert discovery.normalize_api_base(address) == expected
