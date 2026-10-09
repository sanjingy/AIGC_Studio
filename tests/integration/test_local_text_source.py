"""本机会员 CLI 写文本（ADR-041）：显式选择 → 真实路由 → 0 平台 Credits。

钉的是这条路径上真正会花错钱的几件事，全部走生产入口（`PATCH model-preference`、
`POST advance`、桥接三端点），唯一的替身是"桌面连接器"——一个按真实桥接协议
poll / 回结果的协程，产出借 `MockLLM` 按 schema 生成。**不起任何真实 CLI，
不花任何订阅额度，也不打任何付费上游。**

1. **白名单不等于改道**：部署开着、项目在白名单里，但没选本机 → 照常走原实现。
2. **选了才走**：项目选 `provider.local:claude` → 请求进 claude 的队列，
   `agent_runs.model_id` 带 `local-cli.` 前缀，Gateway 一次都没被调到。
3. **0 平台 Credits**：本机文本跑完，余额、预扣、流水一样都不动。
4. **不回落**：选了本机、连接器离线 → 409 `local_runtime.text_offline`；
   部署关了试点 → 真实 Gateway 也拒绝（`local_runtime.text_not_configured`），
   平台路由上的付费上游一次都没被调到。
5. **选不了假的**：不在可选名单（Codex 未开放）、不在白名单、连接器不在线、
   拿去配出图、配成组织默认，一律存不进去。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable, Iterator

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from adapters.providers.base import TextRequest, TextResponse
from apps.api.core.config import Settings
from apps.api.modules.agent import llm
from apps.api.modules.billing import pricing
from apps.api.modules.gateway import breaker
from apps.api.modules.gateway import service as gw
from apps.api.modules.local_runtime import router as lr_router
from apps.api.modules.local_runtime import service as lr_service
from apps.api.modules.local_runtime import transport

pytestmark = pytest.mark.integration

P = "/api/v1/projects"
C = "/api/v1/credits"
LR = "/api/v1/local-runtime"
TOKEN = "bridge-" + "m" * 40
LOCAL_CLAUDE = "provider.local:claude"
LOCAL_CODEX = "provider.local:codex"
NOVEL = "把这篇小说做成 5 分钟悬疑漫剧，主角是一名侦探。"


# ---------------------------------------------------------------- 夹具


async def _org_id(client: AsyncClient) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/v1/auth/me")).json()["org_id"])


async def _project(client: AsyncClient, title: str = "本机会员文本") -> uuid.UUID:
    r = await client.post(P, json={"title": title})
    assert r.status_code == 201, r.text
    return uuid.UUID(r.json()["id"])


async def _balance(client: AsyncClient) -> dict[str, int]:
    return dict((await client.get(f"{C}/balance")).json())


async def _ledger(client: AsyncClient) -> list[dict]:
    return list((await client.get(f"{C}/transactions")).json())


def _bridge() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


Enable = Callable[..., None]


@pytest.fixture
def enable_pilot(monkeypatch: pytest.MonkeyPatch) -> Enable:
    """打开试点。`get_settings` 在 router / service 里是导入时绑定的，两边都打。"""

    def _enable(
        org_id: uuid.UUID,
        project_ids: list[uuid.UUID],
        text_providers: list[str] | None = None,
    ) -> None:
        kwargs: dict[str, object] = {
            "env": "test",
            "local_cli_enabled": True,
            "local_cli_org_id": org_id,
            "local_cli_project_ids": project_ids,
            "local_cli_provider": "claude",
            "local_cli_text_providers": text_providers or [],
            "local_cli_token": TOKEN,
            "local_cli_timeout_seconds": 30,
            "local_cli_image_timeout_seconds": 60,
        }
        settings = Settings(**kwargs)  # type: ignore[arg-type]
        monkeypatch.setattr(lr_router, "get_settings", lambda: settings)
        monkeypatch.setattr(lr_service, "get_settings", lambda: settings)

    return _enable


@pytest.fixture
async def clean_mailbox() -> AsyncIterator[None]:
    yield
    for provider in ("codex", "claude"):
        r = transport._r()
        try:
            await r.delete(
                transport.queue_key(provider, "text"),
                transport.queue_key(provider, "image"),
                transport.heartbeat_key(provider),
            )
        finally:
            await transport._release(r)


class _Tripwire:
    """不许被走到的那条原实现。走到一次就记一次，断言里看。"""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request: llm.LLMRequest) -> llm.LLMResponse:
        del request
        self.calls += 1
        raise AssertionError("选了本机之后不许回落到原实现")


@pytest.fixture
def routing() -> Iterator[Callable[[llm.LLMProvider], None]]:
    def _install(fallback: llm.LLMProvider) -> None:
        llm.set_provider(llm.RoutingLLM(fallback))

    yield _install
    llm.set_provider(None)


async def _select(
    client: AsyncClient, pid: uuid.UUID, value: str | None, capability: str = "text_generation"
):
    return await client.patch(
        f"{P}/{pid}/model-preference", json={"capability": capability, "model_id": value}
    )


class _Connector:
    """按真实桥接协议干活的"桌面连接器"。产出借 MockLLM 按 schema 生成。"""

    def __init__(self, client: AsyncClient, provider: str) -> None:
        self._client = client
        self._provider = provider
        self._mock = llm.MockLLM()
        self.served: list[str] = []
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            r = await self._client.post(
                f"{LR}/poll",
                json={"provider": self._provider, "kinds": ["text"]},
                headers=_bridge(),
            )
            if r.status_code == 204:
                await asyncio.sleep(0.05)
                continue
            assert r.status_code == 200, r.text
            job = r.json()
            text = job["text"]
            answer = await self._mock.complete(
                llm.LLMRequest(
                    system=text["system"],
                    user=text["user"],
                    schema_name=text["schema_name"],
                    max_output_tokens=text["max_output_tokens"],
                )
            )
            self.served.append(text["schema_name"])
            done = await self._client.post(
                f"{LR}/requests/{job['request_id']}/result",
                json={
                    "lease_token": job["lease_token"],
                    "text": answer.text,
                    # 故意报一个和平台目录同名的模型：计费不许按名查价
                    "model_id": "deepseek-chat",
                },
                headers=_bridge(),
            )
            assert done.status_code == 204, done.text

    async def __aenter__(self) -> _Connector:
        self._task = asyncio.create_task(self._loop())
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._stop.set()
        assert self._task is not None
        await asyncio.wait_for(self._task, timeout=10)


# ---------------------------------------------------------------- 选择


class TestSelection:
    async def test_connected_claude_can_be_selected_and_cleared(
        self, alice: AsyncClient, enable_pilot: Enable, clean_mailbox: None
    ) -> None:
        pid = await _project(alice)
        enable_pilot(await _org_id(alice), [pid])
        await transport.touch_heartbeat("claude", kinds=["text"], version="2.1.294 (Claude Code)")

        r = await _select(alice, pid, LOCAL_CLAUDE)
        assert r.status_code == 200, r.text
        assert r.json()["model_preference"]["text_generation"] == LOCAL_CLAUDE

        body = (await alice.get(f"{LR}/status")).json()
        assert body["projects"] == [
            {"project_id": str(pid), "title": "本机会员文本", "provider": "claude"}
        ]
        claude = next(p for p in body["text_providers"] if p["provider"] == "claude")
        assert claude == {
            "provider": "claude",
            "connected": True,
            "ready": True,
            "version": "2.1.294 (Claude Code)",
            "reason": None,
        }
        assert "token" not in str(body).lower()

        cleared = await _select(alice, pid, None)
        assert cleared.status_code == 200
        assert "text_generation" not in cleared.json()["model_preference"]

    async def test_offline_runner_cannot_be_selected(
        self, alice: AsyncClient, enable_pilot: Enable, clean_mailbox: None
    ) -> None:
        pid = await _project(alice)
        enable_pilot(await _org_id(alice), [pid])

        r = await _select(alice, pid, LOCAL_CLAUDE)
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "local_runtime.text_offline"
        project = (await alice.get(f"{P}/{pid}")).json()
        assert project["model_preference"] == {}, "被拒的选择不许落库"

    async def test_codex_not_offered_is_not_selectable_even_if_online(
        self, alice: AsyncClient, enable_pilot: Enable, clean_mailbox: None
    ) -> None:
        """可选名单里没有 Codex（例如它还没 `codex login`）时，连上了也选不了。"""
        pid = await _project(alice)
        enable_pilot(await _org_id(alice), [pid])  # 名单只有 claude
        await transport.touch_heartbeat("codex", kinds=["text", "image"])

        r = await _select(alice, pid, LOCAL_CODEX)
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "local_runtime.text_not_configured"
        body = (await alice.get(f"{LR}/status")).json()
        assert [p["provider"] for p in body["text_providers"]] == ["claude"]

    async def test_offered_but_logged_out_codex_shows_as_unavailable_with_repair_hint(
        self, alice: AsyncClient, enable_pilot: Enable, clean_mailbox: None
    ) -> None:
        """Codex 开放了但没登录：连接器起不来 = 没有心跳 → 未连接，并告诉用户怎么修。"""
        pid = await _project(alice)
        enable_pilot(await _org_id(alice), [pid], text_providers=["claude", "codex"])

        body = (await alice.get(f"{LR}/status")).json()
        codex = next(p for p in body["text_providers"] if p["provider"] == "codex")
        assert codex["connected"] is False
        assert codex["ready"] is False
        assert "codex login" in codex["reason"]
        assert body["capabilities"]["video"] is False

        r = await _select(alice, pid, LOCAL_CODEX)
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "local_runtime.text_offline"

    async def test_project_outside_allowlist_cannot_select(
        self, alice: AsyncClient, enable_pilot: Enable, clean_mailbox: None
    ) -> None:
        listed, other = await _project(alice), await _project(alice, "白名单外")
        enable_pilot(await _org_id(alice), [listed])
        await transport.touch_heartbeat("claude", kinds=["text"])

        r = await _select(alice, other, LOCAL_CLAUDE)
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "local_runtime.text_not_configured"

    async def test_disabled_pilot_cannot_select(self, alice: AsyncClient) -> None:
        pid = await _project(alice)
        r = await _select(alice, pid, LOCAL_CLAUDE)
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "local_runtime.text_not_configured"

    async def test_local_cli_is_text_only(
        self, alice: AsyncClient, enable_pilot: Enable, clean_mailbox: None
    ) -> None:
        pid = await _project(alice)
        enable_pilot(await _org_id(alice), [pid])
        await transport.touch_heartbeat("claude", kinds=["text"])

        r = await _select(alice, pid, LOCAL_CLAUDE, capability="image_generation")
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "provider.params.invalid"

    async def test_local_cli_cannot_be_the_org_default(
        self, alice: AsyncClient, enable_pilot: Enable, clean_mailbox: None
    ) -> None:
        """只能逐项目选：组织默认会把白名单外的项目一起指过去，那些项目一跑就报错。"""
        pid = await _project(alice)
        enable_pilot(await _org_id(alice), [pid])
        await transport.touch_heartbeat("claude", kinds=["text"])

        r = await alice.put(
            "/api/v1/model-config/text_generation",
            json={"provider_id": LOCAL_CLAUDE, "model_id": None, "key_source": "platform"},
        )
        assert 400 <= r.status_code < 500, r.text

    async def test_other_tenant_cannot_select_on_my_project(
        self, alice: AsyncClient, bob: AsyncClient, enable_pilot: Enable, clean_mailbox: None
    ) -> None:
        pid = await _project(alice)
        enable_pilot(await _org_id(alice), [pid])
        await transport.touch_heartbeat("claude", kinds=["text"])

        r = await _select(bob, pid, LOCAL_CLAUDE)
        assert r.status_code == 404


# ---------------------------------------------------------------- 路由 + 计费


class TestRoutingAndZeroCredits:
    async def test_allowlisted_but_unselected_project_keeps_its_old_path(
        self,
        alice: AsyncClient,
        enable_pilot: Enable,
        clean_mailbox: None,
        routing: Callable[[llm.LLMProvider], None],
    ) -> None:
        """白名单 ≠ 改道。以前白名单内的项目会被自动送去本机，用户不知情。"""
        pid = await _project(alice)
        enable_pilot(await _org_id(alice), [pid])
        routing(llm.MockLLM())

        async with _Connector(alice, "claude") as connector:
            r = await alice.post(f"{P}/{pid}/advance", json={"user_input": NOVEL})
        assert r.status_code == 200, r.text
        assert connector.served == [], "没选本机就不该有任何请求进桌面队列"

        runs = (await alice.get(f"{P}/{pid}/agent-runs")).json()
        assert runs and all(run["model_id"] == llm.MockLLM.model_id for run in runs)

    async def test_selected_claude_runs_locally_and_moves_no_money(
        self,
        alice: AsyncClient,
        db: AsyncSession,
        enable_pilot: Enable,
        clean_mailbox: None,
        routing: Callable[[llm.LLMProvider], None],
    ) -> None:
        pid = await _project(alice)
        org_id = await _org_id(alice)
        enable_pilot(org_id, [pid])
        tripwire = _Tripwire()
        routing(tripwire)

        await transport.touch_heartbeat("claude", kinds=["text"])
        assert (await _select(alice, pid, LOCAL_CLAUDE)).status_code == 200
        balance_before, ledger_before = await _balance(alice), await _ledger(alice)

        async with _Connector(alice, "claude") as connector:
            r = await alice.post(f"{P}/{pid}/advance", json={"user_input": NOVEL})
        assert r.status_code == 200, r.text

        assert connector.served, "请求必须真的进了 claude 的队列、被桌面取走"
        assert tripwire.calls == 0, "原实现一次都不许被调到"

        runs = (await alice.get(f"{P}/{pid}/agent-runs")).json()
        assert runs
        for run in runs:
            assert run["status"] == "succeeded"
            # 自报的 deepseek-chat 被加上前缀：成本复盘与计费都认得出这是会员额度
            assert run["model_id"] == "local-cli.claude:deepseek-chat"

        assert await _balance(alice) == balance_before, "本机会员文本不扣平台 Credits"
        assert await _ledger(alice) == ledger_before, "也不留下任何流水（含 0 元预扣）"

        # 同一组 model_id 走计费函数也是 0——将来文本链路接计费时这条仍成立
        for run in runs:
            assert (
                await pricing.text_run_cost(
                    db, model_id=run["model_id"], tokens_out=1_000_000, org_id=org_id
                )
                == 0
            )
        assert (
            await pricing.estimate_agent_run(db, budget_credits=500, org_id=org_id, project_id=pid)
            == 0
        )

    async def test_offline_runner_fails_fast_without_fallback_or_charge(
        self,
        alice: AsyncClient,
        enable_pilot: Enable,
        clean_mailbox: None,
        routing: Callable[[llm.LLMProvider], None],
    ) -> None:
        pid = await _project(alice)
        enable_pilot(await _org_id(alice), [pid])
        tripwire = _Tripwire()
        routing(tripwire)

        await transport.touch_heartbeat("claude", kinds=["text"])
        assert (await _select(alice, pid, LOCAL_CLAUDE)).status_code == 200
        r = transport._r()
        try:
            await r.delete(transport.heartbeat_key("claude"))  # 用户关了电脑
        finally:
            await transport._release(r)
        balance_before, ledger_before = await _balance(alice), await _ledger(alice)

        r = await alice.post(f"{P}/{pid}/advance", json={"user_input": NOVEL})

        assert r.status_code == 409, r.text
        err = r.json()["error"]
        assert err["code"] == "local_runtime.text_offline"
        assert "备用通道" not in err["user_message"]
        assert "付费" in err["user_message"], "要明说没有改用付费模型"
        assert tripwire.calls == 0
        assert await transport.queue_depth("claude", "text") == 0
        assert await _balance(alice) == balance_before
        assert await _ledger(alice) == ledger_before

    async def test_pilot_switched_off_after_selection_never_reaches_a_paid_upstream(
        self,
        alice: AsyncClient,
        enable_pilot: Enable,
        clean_mailbox: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """部署收回了试点，项目里还存着"本机"。真实 Gateway 必须拒绝，
        不许把它当成过期偏好、落到平台路由上那家付费上游。"""
        pid = await _project(alice)
        enable_pilot(await _org_id(alice), [pid])
        await transport.touch_heartbeat("claude", kinds=["text"])
        assert (await _select(alice, pid, LOCAL_CLAUDE)).status_code == 200
        monkeypatch.undo()  # 试点关掉

        paid = _CountingUpstream()
        reg = gw.Registry()
        reg.add(
            "text_generation",
            gw.Route(paid.provider_id, paid.model_id, priority=100, factory=lambda: paid),  # type: ignore[arg-type,return-value]
        )
        gw._registry = reg
        await breaker.reset(paid.provider_id)
        llm.set_provider(llm.GatewayLLM())
        balance_before = await _balance(alice)
        try:
            r = await alice.post(f"{P}/{pid}/advance", json={"user_input": NOVEL})
        finally:
            llm.set_provider(None)
            gw.reset_registry()

        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "local_runtime.text_not_configured"
        assert paid.calls == 0, "付费上游一次都不许被调到"
        assert await _balance(alice) == balance_before


class _CountingUpstream:
    provider_id = "p.paid"
    model_id = "paid-model"

    def __init__(self) -> None:
        self.calls = 0

    async def generate_text(self, request: TextRequest) -> TextResponse:
        del request
        self.calls += 1
        raise AssertionError("不许被调到")


# ---------------------------------------------------------------- 计费函数（API 路径不变）


class TestPricingBoundary:
    async def test_api_text_cost_is_unchanged_and_local_prefix_is_free(
        self, db: AsyncSession
    ) -> None:
        priced = await pricing.text_run_cost(db, model_id="deepseek-chat", tokens_out=600_000)
        assert priced > 0, "平台模型的文本定价不受影响"
        assert (
            await pricing.text_run_cost(
                db, model_id="local-cli.claude:deepseek-chat", tokens_out=600_000
            )
            == 0
        )

    async def test_estimate_without_local_selection_is_the_budget(
        self, alice: AsyncClient, db: AsyncSession
    ) -> None:
        pid = await _project(alice)
        org_id = await _org_id(alice)
        assert (
            await pricing.estimate_agent_run(db, budget_credits=500, org_id=org_id, project_id=pid)
            == 500
        )
