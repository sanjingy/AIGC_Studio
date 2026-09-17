"""Gateway 路由、凭据绑定与 failover 决策。

不打真实 Provider——用假 Provider 验证控制流。
真实调用的正确性靠手工探测确认过一次，之后不该在每次测试里重复付费。
"""

from __future__ import annotations

from typing import Any

import pytest

from adapters.providers.base import TextRequest, TextResponse
from apps.api.core.errors import AppError
from apps.api.modules.gateway import breaker
from apps.api.modules.gateway import service as gw


class FakeProvider:
    def __init__(self, *, provider_id: str, api_key: str, fail_with: str | None = None) -> None:
        self.provider_id = provider_id
        self.model_id = "fake"
        self.api_key = api_key
        self._fail_with = fail_with
        self.calls = 0

    async def generate_text(self, request: TextRequest) -> TextResponse:
        self.calls += 1
        if self._fail_with:
            raise AppError(self._fail_with, message="fake failure")
        return TextResponse(
            text='{"ok": true}',
            tokens_in=1,
            tokens_out=1,
            model_id=f"{self.provider_id}:{self.api_key[-4:]}",
        )


def _registry_with(*providers: FakeProvider) -> gw.Registry:
    reg = gw.Registry()
    for i, p in enumerate(providers):
        reg.add(
            "text_generation",
            gw.Route(p.provider_id, p.model_id, priority=100 - i, factory=lambda p=p: p),  # type: ignore[misc]
        )
    return reg


@pytest.fixture(autouse=True)
def _clean_registry() -> Any:
    gw.reset_registry()
    yield
    gw.reset_registry()


# ------------------------------------------------------------------ 凭据绑定


def test_each_route_binds_its_own_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """每条路由必须拿到自己那家的 Key。

    回归用例：`_build` 里曾用 lambda 闭包捕获共享的 `key` 变量，
    等 factory 真正被调用时 `key` 已指向后面赋值的另一家凭据，
    结果 DeepSeek 拿着万相的 Key 去请求，报 "api key invalid"。
    lambda 捕获变量、partial 捕获值——这里必须是后者。
    """
    from apps.api.core import config

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-AAAA")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-dashscope-BBBB")
    config.get_settings.cache_clear()
    gw.reset_registry()

    try:
        for capability, expected in (
            ("text_generation", "sk-deepseek-AAAA"),
            ("image_generation", "sk-dashscope-BBBB"),
        ):
            for route in gw.registry().for_capability(capability):
                provider = route.factory()
                assert provider._api_key == expected, (
                    f"{capability}/{route.model_id} 拿到了错误的凭据"
                )
    finally:
        config.get_settings.cache_clear()
        gw.reset_registry()


def test_missing_key_means_no_route(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.api.core import config

    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    config.get_settings.cache_clear()
    gw.reset_registry()
    try:
        assert gw.registry().for_capability("text_generation") == []
    finally:
        config.get_settings.cache_clear()
        gw.reset_registry()


# ------------------------------------------------------------------ 优先级


async def test_highest_priority_wins() -> None:
    primary = FakeProvider(provider_id="p.primary", api_key="key-1111")
    backup = FakeProvider(provider_id="p.backup", api_key="key-2222")
    gw._registry = _registry_with(primary, backup)
    await breaker.reset("p.primary")
    await breaker.reset("p.backup")

    resp = await gw.generate_text(TextRequest(system="s", user="u"))
    assert resp.model_id.endswith("1111")
    assert backup.calls == 0


# ------------------------------------------------------------------ failover


async def test_failover_on_retryable_upstream_error() -> None:
    """上游不可用要自动切下一家。"""
    down = FakeProvider(provider_id="p.down", api_key="k1", fail_with="provider.unavailable")
    up = FakeProvider(provider_id="p.up", api_key="k2")
    gw._registry = _registry_with(down, up)
    await breaker.reset("p.down")
    await breaker.reset("p.up")

    resp = await gw.generate_text(TextRequest(system="s", user="u"))
    assert resp.model_id.startswith("p.up")
    assert down.calls == 1 and up.calls == 1


async def test_no_failover_on_param_error() -> None:
    """参数错误换一家还是错，不该白花第二次钱。

    该不该 failover 由错误目录决定（21_ErrorTaxonomy.md §2），
    不在 Gateway 里另写一套判断。
    """
    bad = FakeProvider(provider_id="p.bad", api_key="k1", fail_with="provider.params.invalid")
    other = FakeProvider(provider_id="p.other", api_key="k2")
    gw._registry = _registry_with(bad, other)
    await breaker.reset("p.bad")
    await breaker.reset("p.other")

    with pytest.raises(AppError) as exc:
        await gw.generate_text(TextRequest(system="s", user="u"))
    assert exc.value.code == "provider.params.invalid"
    assert other.calls == 0, "参数错误不该再试下一家"


async def test_no_failover_on_content_rejection() -> None:
    """内容被拒换一家大概率还是被拒，且又花一次钱。"""
    rejected = FakeProvider(
        provider_id="p.rej", api_key="k1", fail_with="provider.content.rejected"
    )
    other = FakeProvider(provider_id="p.other2", api_key="k2")
    gw._registry = _registry_with(rejected, other)
    await breaker.reset("p.rej")
    await breaker.reset("p.other2")

    with pytest.raises(AppError):
        await gw.generate_text(TextRequest(system="s", user="u"))
    assert other.calls == 0


async def test_all_down_raises_last_error() -> None:
    a = FakeProvider(provider_id="p.a", api_key="k", fail_with="provider.unavailable")
    b = FakeProvider(provider_id="p.b", api_key="k", fail_with="provider.rate_limit.exceeded")
    gw._registry = _registry_with(a, b)
    await breaker.reset("p.a")
    await breaker.reset("p.b")

    with pytest.raises(AppError) as exc:
        await gw.generate_text(TextRequest(system="s", user="u"))
    assert exc.value.code == "provider.rate_limit.exceeded"


async def test_no_provider_registered_is_explicit() -> None:
    """没配 Key 时的报错要说清原因，不能只丢一个通用错误。

    期望按**当前真实文案**校准：两条自动兜底（文本退回 MockLLM、出图退回
    mock_image）撤掉之后，"一个模型都没有"如实走到 `_resolve` 的那条分支，
    文案是「没有可用的模型来完成「…」。请在设置页配置你自己的 Provider Key…」
    ——里面没有「API Key」这个词组，旧的正则因此对不上。

    **不放松成"随便抛个异常就算过"**：这条用例的价值全在"用户看得懂、且知道
    该去哪儿配"。所以仍然钉三样：错误码、"没有可用的模型"这句判断、以及那句
    把用户指向配置入口的 Key 提示。
    """
    gw._registry = gw.Registry()
    with pytest.raises(AppError, match="没有可用的模型") as exc:
        await gw.generate_text(TextRequest(system="s", user="u"))
    assert exc.value.code == "provider.unavailable"
    assert "Key" in exc.value.message, "文案要把用户指到配置 Key 的地方"


# ------------------------------------------------------------------ 熔断


async def test_breaker_degrades_after_repeated_failures() -> None:
    """连续失败要降级，否则每次请求都会先打一遍已知挂掉的上游。"""
    pid = "p.flaky"
    await breaker.reset(pid)

    for _ in range(breaker.FAIL_THRESHOLD):
        await breaker.record_failure(pid)

    assert (await breaker.snapshot(pid)).state is breaker.State.DEGRADED
    await breaker.reset(pid)


async def test_breaker_goes_down_then_probes() -> None:
    pid = "p.dead"
    await breaker.reset(pid)

    for _ in range(breaker.DOWN_THRESHOLD):
        await breaker.record_failure(pid)
    assert (await breaker.snapshot(pid)).state is breaker.State.DOWN

    # DOWN 之后短期内不放流量，等探测窗口
    assert await breaker.allows(pid) is False
    await breaker.reset(pid)


async def test_breaker_recovers_after_consecutive_successes() -> None:
    pid = "p.recover"
    await breaker.reset(pid)
    for _ in range(breaker.FAIL_THRESHOLD):
        await breaker.record_failure(pid)

    for _ in range(breaker.RECOVER_SUCCESSES):
        await breaker.record_success(pid)

    assert (await breaker.snapshot(pid)).state is breaker.State.HEALTHY
    await breaker.reset(pid)


async def test_breaker_state_is_shared_not_per_process() -> None:
    """熔断状态必须存 Redis。

    存进程内存的话，8 个 Worker 会各自把已知挂掉的上游再打 5 次，
    总共 40 次无效请求——等于没熔断。
    """
    pid = "p.shared"
    await breaker.reset(pid)
    await breaker.record_failure(pid)

    from apps.api.core.redis import Keys, get_redis

    raw = await get_redis().hgetall(Keys.circuit_breaker(pid))
    assert raw.get("failures") == "1", "熔断计数没有落到 Redis"
    await breaker.reset(pid)
