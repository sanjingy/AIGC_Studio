"""模型上游的三层判定（项目选择 > 组织默认 > 平台目录默认）与 Gateway 接线。

`upstreams.decide` 是 Gateway 调用解析和计费 BYOK 判断**共用**的纯函数，
所以这里的每一条都同时是"调谁"和"按什么价收"的结论。

Gateway 那一半用真实目录 + 假适配器：断言落在"实际被构造出来的适配器
收到了什么"，路由表里写了什么不算数。零网络、零数据库。
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any, ClassVar

import pytest

from adapters.providers.base import KeySource, TextRequest, TextResponse
from adapters.providers.openai_compat import OpenAICompatTextProvider
from apps.api.core.errors import AppError
from apps.api.modules.billing.credentials import ResolvedKey
from apps.api.modules.gateway import breaker, catalog, upstreams
from apps.api.modules.gateway import service as gw

TEXT = "text_generation"
IMAGE = "image_generation"
CUSTOM = catalog.CUSTOM_TEXT_PROVIDER_ID


def _row(provider: str, model: str | None = None, key: str = "platform") -> upstreams.DefaultRow:
    return upstreams.DefaultRow(provider_id=provider, model_id=model, key_source=key)


# ------------------------------------------------------------------ 目录形状


def test_catalog_model_ids_unique_within_capability() -> None:
    """旧的项目偏好只存模型 id，靠它反查 Provider——同能力下重名就查不唯一。"""
    for capability in {spec.capability for spec in catalog.SPECS}:
        ids = catalog.model_ids(capability)
        assert len(ids) == len(set(ids)), capability


def test_custom_endpoint_only_for_text() -> None:
    assert catalog.supports_custom_endpoint(TEXT)
    for capability in (IMAGE, "image_to_video", "text_to_speech", "text_to_video"):
        assert not catalog.supports_custom_endpoint(capability)


# ------------------------------------------------------------------ 三层顺序


def test_platform_default_when_nothing_saved() -> None:
    d = upstreams.decide(TEXT, preference=None, default=None)
    assert (d.provider_id, d.model_id, d.layer, d.key_source) == (
        "provider.deepseek",
        None,
        "platform",
        None,
    )


def test_org_default_beats_platform() -> None:
    d = upstreams.decide(
        IMAGE, preference=None, default=_row("provider.dashscope", "wan2.2-t2i-plus", "org")
    )
    assert (d.provider_id, d.model_id, d.layer, d.key_source) == (
        "provider.dashscope",
        "wan2.2-t2i-plus",
        "org",
        KeySource.ORG,
    )


def test_project_beats_org() -> None:
    d = upstreams.decide(
        IMAGE,
        preference="wan2.2-t2i-flash",
        default=_row("provider.dashscope", "wan2.2-t2i-plus", "org"),
    )
    assert (d.model_id, d.layer) == ("wan2.2-t2i-flash", "project")
    # 同一家：组织对这家显式选的计费来源仍然生效
    assert d.key_source is KeySource.ORG


def test_project_custom_endpoint_beats_org_deepseek() -> None:
    d = upstreams.decide(
        TEXT, preference=CUSTOM, default=_row("provider.deepseek", "deepseek-chat", "platform")
    )
    assert (d.provider_id, d.layer, d.key_source) == (CUSTOM, "project", KeySource.ORG)


def test_stale_project_preference_falls_through_to_org_default() -> None:
    d = upstreams.decide(
        TEXT, preference="model-that-was-retired", default=_row(CUSTOM, None, "org")
    )
    assert (d.provider_id, d.layer) == (CUSTOM, "org")


def test_retired_org_provider_is_rejected_not_swapped() -> None:
    """组织选的那家下线了：报错，不静默落到平台默认那家。"""
    with pytest.raises(AppError) as exc:
        upstreams.decide(TEXT, preference=None, default=_row("provider.gone", None, "org"))
    assert exc.value.code == "provider.byok.rejected"


def test_custom_endpoint_cannot_serve_image() -> None:
    """图片能力的组织默认指向自定义端点——那条路根本不存在，按下线处理。"""
    with pytest.raises(AppError):
        upstreams.decide(IMAGE, preference=None, default=_row(CUSTOM, None, "org"))


def test_custom_sentinel_preference_is_ignored_for_image() -> None:
    d = upstreams.decide(IMAGE, preference=CUSTOM, default=None)
    assert d.provider_id != CUSTOM


# ------------------------------------------------------------------ Gateway 接线


class RecordingDeepSeek:
    provider_id = "provider.deepseek"
    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(
        self, *, api_key: str, model_id: str, key_source: KeySource = KeySource.PLATFORM
    ) -> None:
        self._api_key = api_key
        self.model_id = model_id
        self.key_source = key_source

    async def generate_text(self, request: TextRequest) -> TextResponse:
        del request
        RecordingDeepSeek.calls.append(
            {"api_key": self._api_key, "model_id": self.model_id, "key_source": self.key_source}
        )
        return TextResponse(text="{}", tokens_in=1, tokens_out=1, model_id=self.model_id)


PLATFORM_KEY = "sk-platform-000000000000"
ORG_KEY = "sk-org-deepseek-1111111111"
ENDPOINT = upstreams.ResolvedEndpoint(
    label="公司网关",
    base_url="https://llm.example.com/v1",
    model_id="qwen2.5-72b-instruct",
    api_key="sk-custom-endpoint-2222222222",
)


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """真实目录（只把 DeepSeek 的适配器换成记录器）+ 三个可控的查库点。"""
    RecordingDeepSeek.calls = []
    spec = catalog.spec_for("provider.deepseek", capability=TEXT)
    assert spec is not None
    monkeypatch.setattr(catalog, "SPECS", (dataclasses.replace(spec, adapter=RecordingDeepSeek),))
    monkeypatch.setattr(catalog, "platform_key", lambda provider_id: PLATFORM_KEY)
    gw.reset_registry()

    state: dict[str, Any] = {"default": None, "key": None, "endpoint": None, "pref": None}

    async def _default(**_kw: Any) -> upstreams.DefaultRow | None:
        return state["default"]  # type: ignore[no-any-return]

    async def _key(*, org_id: uuid.UUID, capability: str, provider_id: str) -> ResolvedKey | None:
        del org_id
        if state["key"] is None:
            return None
        return ResolvedKey(provider_id=provider_id, api_key=state["key"], capability=capability)

    async def _endpoint(**_kw: Any) -> upstreams.ResolvedEndpoint | None:
        return state["endpoint"]  # type: ignore[no-any-return]

    async def _pref(**_kw: Any) -> str | None:
        return state["pref"]  # type: ignore[no-any-return]

    monkeypatch.setattr(gw, "_load_org_default", _default)
    monkeypatch.setattr(gw, "_load_org_key", _key)
    monkeypatch.setattr(gw, "_load_custom_endpoint", _endpoint)
    monkeypatch.setattr(gw, "_load_model_preference", _pref)
    yield state
    gw.reset_registry()


async def test_saved_custom_endpoint_builds_the_adapter_with_saved_values(
    wired: dict[str, Any],
) -> None:
    """页面存的 base_url / 模型 / Key，就是 Gateway 构造适配器时用的那三样。"""
    wired["default"] = _row(CUSTOM, None, "org")
    wired["endpoint"] = ENDPOINT

    resolution = await gw._resolve(TEXT, org_id=uuid.uuid4())

    assert resolution.key_source is KeySource.ORG
    (route,) = resolution.routes
    assert (route.provider_id, route.model_id) == (CUSTOM, ENDPOINT.model_id)
    adapter = route.factory()
    assert isinstance(adapter, OpenAICompatTextProvider)
    assert adapter._base_url == ENDPOINT.base_url
    assert adapter._api_key == ENDPOINT.api_key
    assert adapter.chat_request_body(TextRequest(system="s", user="u"))["model"] == (
        ENDPOINT.model_id
    )
    # 只有这一条：自定义端点失败不回落平台 DeepSeek
    assert RecordingDeepSeek.calls == []


async def test_custom_selected_but_missing_is_an_error_not_platform(wired: dict[str, Any]) -> None:
    wired["default"] = _row(CUSTOM, None, "org")
    with pytest.raises(AppError) as exc:
        await gw.generate_text(TextRequest(system="s", user="u"), org_id=uuid.uuid4())
    assert exc.value.code == "provider.byok.rejected"
    assert RecordingDeepSeek.calls == []


async def test_explicit_platform_billing_ignores_stored_key(wired: dict[str, Any]) -> None:
    """存着自己的 Key，但组织显式选了平台额度——用平台 Key。"""
    wired["default"] = _row("provider.deepseek", "deepseek-v4-flash", "platform")
    wired["key"] = ORG_KEY
    org = uuid.uuid4()
    await breaker.reset(breaker.scope("provider.deepseek"))

    await gw.generate_text(TextRequest(system="s", user="u"), org_id=org)

    (call,) = RecordingDeepSeek.calls
    assert call == {
        "api_key": PLATFORM_KEY,
        "model_id": "deepseek-v4-flash",
        "key_source": KeySource.PLATFORM,
    }


async def test_explicit_org_billing_uses_org_key_and_saved_model(wired: dict[str, Any]) -> None:
    wired["default"] = _row("provider.deepseek", "deepseek-v4-flash", "org")
    wired["key"] = ORG_KEY
    org = uuid.uuid4()
    await breaker.reset(breaker.scope("provider.deepseek", org_id=org))

    await gw.generate_text(TextRequest(system="s", user="u"), org_id=org)

    assert RecordingDeepSeek.calls[0] == {
        "api_key": ORG_KEY,
        "model_id": "deepseek-v4-flash",
        "key_source": KeySource.ORG,
    }


async def test_explicit_org_billing_without_key_fails_loudly(wired: dict[str, Any]) -> None:
    """选了"用自己的 Key"但 Key 不在了：报错，不拿平台 Key 顶上。"""
    wired["default"] = _row("provider.deepseek", None, "org")
    with pytest.raises(AppError) as exc:
        await gw.generate_text(TextRequest(system="s", user="u"), org_id=uuid.uuid4())
    assert exc.value.code == "provider.byok.rejected"
    assert RecordingDeepSeek.calls == []


async def test_project_custom_override_beats_org_platform_default(wired: dict[str, Any]) -> None:
    wired["default"] = _row("provider.deepseek", "deepseek-chat", "platform")
    wired["endpoint"] = ENDPOINT
    wired["pref"] = CUSTOM

    preferred = await gw._preferred_model(
        capability=TEXT, org_id=uuid.uuid4(), project_id=uuid.uuid4(), allow_reasoning=True
    )
    resolution = await gw._resolve(TEXT, org_id=uuid.uuid4(), preferred_model_id=preferred)
    assert [r.provider_id for r in resolution.routes] == [CUSTOM]
    assert resolution.key_source is KeySource.ORG
