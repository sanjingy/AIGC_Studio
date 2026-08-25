"""BYOK 在 Gateway 里的接线（ADR-027）。

这组用例回答的是一个财务问题，不是路由问题：**配了自己 Key 的用户，
平台到底有没有真的用他那把 Key 去调上游。** 上一轮把计费的 BYOK 折扣做完了，
但调用仍然走平台 Key——账面上用户少付钱，实际成本平台自己吃，
每一次生成都在亏。所以断言必须落在"实际拿到 Key 的那个对象"上，
只看计费数字或只看路由表都证明不了这件事。

不打真实上游：假适配器记录自己收到的 Key 与来源标记。
"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

import pytest

from adapters.providers.base import KeySource, TextRequest, TextResponse
from apps.api.core.errors import AppError
from apps.api.modules.billing.credentials import ResolvedKey
from apps.api.modules.gateway import breaker, catalog
from apps.api.modules.gateway import service as gw

PLATFORM_KEY = "sk-platform-0000"
ORG_KEY = "sk-org-own-key-9999"
CAPABILITY = "text_generation"
FAKE_PROVIDER = "provider.fake"


class RecordingProvider:
    """记下自己被喂了哪把 Key、被标成什么来源。

    断言点就在这里：它是**真正会把 Key 放进 Authorization 头**的那一层的
    替身，路由表里写了什么不算数，传到这里的才算数。
    """

    provider_id = FAKE_PROVIDER
    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(
        self, *, api_key: str, model_id: str, key_source: KeySource = KeySource.PLATFORM
    ) -> None:
        self._api_key = api_key
        self.model_id = model_id
        self.key_source = key_source

    async def generate_text(self, request: TextRequest) -> TextResponse:
        del request
        RecordingProvider.calls.append(
            {"api_key": self._api_key, "key_source": self.key_source, "model_id": self.model_id}
        )
        if self._api_key == FAILING_KEY:
            # 上游 401 常把收到的 Key 原样贴回来，这里照抄这个恶习——
            # 脱敏那条防线只有在被这么打一下时才证明得了自己有效。
            raise AppError(
                "provider.account.insufficient",
                message=f"Authentication Fails, your api key: {self._api_key} is invalid",
            )
        return TextResponse(text='{"ok": true}', tokens_in=1, tokens_out=1, model_id=self.model_id)


FAILING_KEY = "sk-org-revoked-key-1234"

_FAKE_SPEC = catalog.ProviderSpec(
    provider_id=FAKE_PROVIDER,
    capability=CAPABILITY,
    label="假 Provider",
    adapter=RecordingProvider,
    models=(("fake-fast", 100), ("fake-slow", 60)),
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> Any:
    """把目录、平台路由表、Key 查询三处都换成可控的假件。

    `_load_org_key` 是这条链路上唯一碰数据库的一环；换掉它之后
    路由/熔断/failover 的行为就能脱离整套 DB 单测。真实的
    "查库 → 解密 → 传给适配器"那一段由集成用例覆盖。
    """
    RecordingProvider.calls = []
    monkeypatch.setattr(catalog, "SPECS", (_FAKE_SPEC,))
    monkeypatch.setattr(catalog, "platform_key", lambda provider_id: PLATFORM_KEY)
    gw.reset_registry()
    yield
    gw.reset_registry()


def _stub_org_key(monkeypatch: pytest.MonkeyPatch, key: str | None) -> None:
    async def _fake(*, org_id: uuid.UUID, capability: str) -> ResolvedKey | None:
        del org_id
        if key is None:
            return None
        return ResolvedKey(provider_id=FAKE_PROVIDER, api_key=key, capability=capability)

    monkeypatch.setattr(gw, "_load_org_key", _fake)


# ------------------------------------------------------------------ 用谁的 Key


async def test_org_with_own_key_calls_upstream_with_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """配了自有 Key → 实际发出去的就是那把，来源标记为 org。"""
    org_id = uuid.uuid4()
    _stub_org_key(monkeypatch, ORG_KEY)
    await breaker.reset(breaker.scope(FAKE_PROVIDER, org_id=org_id))

    await gw.generate_text(TextRequest(system="s", user="u"), org_id=org_id)

    assert len(RecordingProvider.calls) == 1
    assert RecordingProvider.calls[0]["api_key"] == ORG_KEY
    assert RecordingProvider.calls[0]["key_source"] is KeySource.ORG


async def test_org_without_own_key_falls_back_to_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """没配 → 一切照旧，平台 Key、平台来源标记。"""
    _stub_org_key(monkeypatch, None)
    await breaker.reset(FAKE_PROVIDER)

    await gw.generate_text(TextRequest(system="s", user="u"), org_id=uuid.uuid4())

    assert RecordingProvider.calls[0]["api_key"] == PLATFORM_KEY
    assert RecordingProvider.calls[0]["key_source"] is KeySource.PLATFORM


async def test_no_tenant_context_uses_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有 org 上下文（脚本、内部调用）时不去查库，直接平台档。"""

    async def _explode(*, org_id: uuid.UUID, capability: str) -> ResolvedKey | None:
        raise AssertionError("没有 org_id 时不该查凭证表")

    monkeypatch.setattr(gw, "_load_org_key", _explode)
    await breaker.reset(FAKE_PROVIDER)

    await gw.generate_text(TextRequest(system="s", user="u"))

    assert RecordingProvider.calls[0]["key_source"] is KeySource.PLATFORM


async def test_key_is_read_per_call_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """每次调用现查。换了 Key 之后下一次调用必须立刻用新的那把——

    缓存住等于"删了 Key 还在用"，而删 Key 的人多半正是因为它泄露了。
    """
    org_id = uuid.uuid4()
    await breaker.reset(breaker.scope(FAKE_PROVIDER, org_id=org_id))

    _stub_org_key(monkeypatch, ORG_KEY)
    await gw.generate_text(TextRequest(system="s", user="u"), org_id=org_id)
    _stub_org_key(monkeypatch, "sk-org-rotated-8888")
    await gw.generate_text(TextRequest(system="s", user="u"), org_id=org_id)

    assert [c["api_key"] for c in RecordingProvider.calls] == [ORG_KEY, "sk-org-rotated-8888"]


# ------------------------------------------------------------------ 熔断作用域


async def test_org_key_failures_do_not_trip_platform_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """自有 Key 被上游拒 → 只熔断这个 org，平台档纹丝不动。

    合成一把熔断的话，一个用户的 Key 被封就会把这家 Provider 整个降级，
    用平台 Key 的其他所有用户跟着遭殃——一个人的账号问题不该有这种
    放大倍数。
    """
    org_id = uuid.uuid4()
    org_scope = breaker.scope(FAKE_PROVIDER, org_id=org_id)
    await breaker.reset(org_scope)
    await breaker.reset(FAKE_PROVIDER)
    _stub_org_key(monkeypatch, FAILING_KEY)

    for _ in range(breaker.FAIL_THRESHOLD):
        with pytest.raises(AppError):
            await gw.generate_text(TextRequest(system="s", user="u"), org_id=org_id)

    assert (await breaker.snapshot(org_scope)).state is not breaker.State.HEALTHY
    platform = await breaker.snapshot(FAKE_PROVIDER)
    assert platform.state is breaker.State.HEALTHY
    assert platform.failures == 0, "用户自己的 Key 出问题不该记到平台档头上"

    await breaker.reset(org_scope)


async def test_one_org_breaker_does_not_affect_another(monkeypatch: pytest.MonkeyPatch) -> None:
    """两个 org 各算各的。"""
    a, b = uuid.uuid4(), uuid.uuid4()
    await breaker.reset(breaker.scope(FAKE_PROVIDER, org_id=a))
    await breaker.reset(breaker.scope(FAKE_PROVIDER, org_id=b))

    _stub_org_key(monkeypatch, FAILING_KEY)
    for _ in range(breaker.FAIL_THRESHOLD):
        with pytest.raises(AppError):
            await gw.generate_text(TextRequest(system="s", user="u"), org_id=a)

    assert (await breaker.snapshot(breaker.scope(FAKE_PROVIDER, org_id=b))).failures == 0
    await breaker.reset(breaker.scope(FAKE_PROVIDER, org_id=a))


# ------------------------------------------------------------------ failover 边界


async def test_byok_failure_never_falls_back_to_platform_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """自有 Key 调不通时**不许**改用平台 Key 重试。

    这正是本轮要堵的洞的镜像：计费已经按 BYOK 折扣算过了，
    这时候平台 Key 顶上去 = 平台掏钱、用户按折扣价付款。
    """
    org_id = uuid.uuid4()
    await breaker.reset(breaker.scope(FAKE_PROVIDER, org_id=org_id))
    _stub_org_key(monkeypatch, FAILING_KEY)

    with pytest.raises(AppError):
        await gw.generate_text(TextRequest(system="s", user="u"), org_id=org_id)

    assert all(c["api_key"] == FAILING_KEY for c in RecordingProvider.calls)
    assert PLATFORM_KEY not in [c["api_key"] for c in RecordingProvider.calls]
    # 而且只打了一次：同一个账号被拒，换个模型问第二遍不会得到别的答案
    assert len(RecordingProvider.calls) == 1
    await breaker.reset(breaker.scope(FAKE_PROVIDER, org_id=org_id))


async def test_byok_error_tells_the_user_it_is_his_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """错误必须能让用户分辨"是我的 Key 有问题"。

    原样透出上游的 `provider.account.insufficient` 是误导：那条的文案是
    "服务暂时不可用，请稍后重试"，而这种情况等下去永远不会好。
    上游的具体分类保留在 detail 里，不另造一套。
    """
    org_id = uuid.uuid4()
    await breaker.reset(breaker.scope(FAKE_PROVIDER, org_id=org_id))
    _stub_org_key(monkeypatch, FAILING_KEY)

    with pytest.raises(AppError) as exc:
        await gw.generate_text(TextRequest(system="s", user="u"), org_id=org_id)

    assert exc.value.code == "provider.byok.rejected"
    assert exc.value.detail["upstream_code"] == "provider.account.insufficient"
    assert "设置页" in exc.value.spec.user_message
    await breaker.reset(breaker.scope(FAKE_PROVIDER, org_id=org_id))


async def test_byok_error_message_never_carries_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """上游把 Key 贴回错误信息里时必须抹掉。

    这条 message 会被 runner 写进 `agent_runs.error_detail` 并进日志，
    不抹掉就等于把用户的明文 Key 落了库——CLAUDE.md 那条红线。
    """
    org_id = uuid.uuid4()
    await breaker.reset(breaker.scope(FAKE_PROVIDER, org_id=org_id))
    _stub_org_key(monkeypatch, FAILING_KEY)

    with pytest.raises(AppError) as exc:
        await gw.generate_text(TextRequest(system="s", user="u"), org_id=org_id)

    assert FAILING_KEY not in exc.value.message
    assert FAILING_KEY[4:16] not in exc.value.message
    await breaker.reset(breaker.scope(FAKE_PROVIDER, org_id=org_id))


async def test_byok_still_failovers_between_models_of_the_same_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一把 Key 换个模型是可以的：同账号同价，钱还是用户自己付。"""
    org_id = uuid.uuid4()
    scope = breaker.scope(FAKE_PROVIDER, org_id=org_id)
    await breaker.reset(scope)
    _stub_org_key(monkeypatch, ORG_KEY)

    calls: list[str] = []
    original = RecordingProvider.generate_text

    async def flaky_first_model(self: RecordingProvider, request: TextRequest) -> TextResponse:
        calls.append(self.model_id)
        if self.model_id == "fake-fast":
            raise AppError("provider.unavailable", message="上游抽风")
        return await original(self, request)

    monkeypatch.setattr(RecordingProvider, "generate_text", flaky_first_model)

    resp = await gw.generate_text(TextRequest(system="s", user="u"), org_id=org_id)

    assert calls == ["fake-fast", "fake-slow"]
    assert resp.model_id == "fake-slow"
    await breaker.reset(scope)


async def test_retired_provider_does_not_silently_use_platform_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """存了 Key 之后平台把这个能力改绑到别家——报错，不偷偷用平台 Key。"""

    async def _fake(*, org_id: uuid.UUID, capability: str) -> ResolvedKey | None:
        del org_id
        return ResolvedKey(provider_id="provider.retired", api_key=ORG_KEY, capability=capability)

    monkeypatch.setattr(gw, "_load_org_key", _fake)

    with pytest.raises(AppError) as exc:
        await gw.generate_text(TextRequest(system="s", user="u"), org_id=uuid.uuid4())

    assert exc.value.code == "provider.byok.rejected"
    assert RecordingProvider.calls == []
