"""一个模型都没有时，用户看到的是"去配置"，不是"正在切换备用通道"。

线上实测（2026-10-06）：测试机没有平台 Key、组织也没接供应商，首页「开始生产」
拿回的是 `provider.unavailable` 的 502 和那句"正在切换备用通道"——可重试、
暗示会自己好，用户只会反复点。真正的原因（一个候选模型都没有）只进了日志。

这里钉四件事：

1. 无平台路由且无组织连接 → advance 返回 `provider.not_configured` 409，
   `user_message` 把人指到模型库，`detail.capability` 说清缺的是哪种能力；
2. 这条路径不花钱：余额、预扣、流水一样都不动；
3. **不扩大**：有候选、只是上游失败时，仍是原来的 `provider.unavailable`；
4. 出图任务以这个码失败时预扣原样退回，且不能重试（重试只会再失败一次）。

ENV=test 下文本一律走 `MockLLM`，碰不到 Gateway，所以前两组用例把真实的
`GatewayLLM` 临时装回去，再把平台路由表换成空的 / 假的——一个真上游都不打。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from httpx import AsyncClient

from adapters.providers.base import TextRequest, TextResponse
from apps.api.core.errors import AppError
from apps.api.modules.agent import llm
from apps.api.modules.gateway import breaker
from apps.api.modules.gateway import service as gw

pytestmark = pytest.mark.integration

P = "/api/v1/projects"
T = "/api/v1/tasks"
C = "/api/v1/credits"

NOVEL = "把这篇小说做成 5 分钟悬疑漫剧，主角是一名侦探。"


class _FailingUpstream:
    """有这条路由、但一调就是上游故障。"""

    provider_id = "p.down"
    model_id = "fake"

    def __init__(self) -> None:
        self.calls = 0

    async def generate_text(self, request: TextRequest) -> TextResponse:
        del request
        self.calls += 1
        raise AppError("provider.unavailable", message="fake upstream 503")


@pytest.fixture
def real_gateway() -> Iterator[None]:
    """把文本链路接回真实 Gateway；路由表由用例自己换。"""
    llm.set_provider(llm.GatewayLLM())
    try:
        yield
    finally:
        llm.set_provider(None)
        gw.reset_registry()


async def _project(client: AsyncClient) -> str:
    return str((await client.post(P, json={"title": "no-model"})).json()["id"])


async def _balance(client: AsyncClient) -> dict[str, int]:
    return dict((await client.get(f"{C}/balance")).json())


async def _ledger(client: AsyncClient) -> list[dict]:
    return list((await client.get(f"{C}/transactions")).json())


async def test_advance_without_any_model_says_go_configure(
    alice: AsyncClient, real_gateway: None
) -> None:
    gw._registry = gw.Registry()  # 平台一条路由都没有；alice 的组织也没有连接
    pid = await _project(alice)
    balance_before, ledger_before = await _balance(alice), await _ledger(alice)

    r = await alice.post(f"{P}/{pid}/advance", json={"user_input": NOVEL})

    assert r.status_code == 409, r.text
    err = r.json()["error"]
    assert err["code"] == "provider.not_configured"
    assert err["retryable"] is False
    assert "模型库" in err["user_message"]
    assert "备用通道" not in err["user_message"], "不能再暗示会自动恢复"
    assert err["detail"]["capability"] == "text_generation"

    assert await _balance(alice) == balance_before, "一次上游都没调，余额与预扣都不该动"
    assert await _ledger(alice) == ledger_before, "也不该留下任何流水"

    # 原文照样先落库：用户配好模型回来不用重贴（orchestrator.advance 的既有约定）
    state = (await alice.get(f"{P}/{pid}/state")).json()
    assert state["current_state_json"]["source"] == NOVEL


async def test_upstream_failure_keeps_the_original_code(
    alice: AsyncClient, real_gateway: None
) -> None:
    """有候选、只是上游挂了：仍是 `provider.unavailable`，不被新码吞掉。"""
    down = _FailingUpstream()
    reg = gw.Registry()
    reg.add(
        "text_generation",
        gw.Route(down.provider_id, down.model_id, priority=100, factory=lambda: down),  # type: ignore[arg-type,return-value]
    )
    gw._registry = reg
    await breaker.reset(down.provider_id)
    pid = await _project(alice)

    r = await alice.post(f"{P}/{pid}/advance", json={"user_input": NOVEL})

    assert down.calls >= 1, "这条用例的前提是上游真的被调到了"
    assert r.status_code == 502, r.text
    assert r.json()["error"]["code"] == "provider.unavailable"


async def test_image_task_failing_with_it_releases_and_is_not_retryable(
    alice: AsyncClient,
) -> None:
    """出图任务在 Worker 里以这个码失败：预扣原样退回，重试被拒。"""
    before = await _balance(alice)
    pid = await _project(alice)
    created = (
        await alice.post(
            T,
            json={
                "type": "mock.fail",
                "project_id": pid,
                "input": {"error_code": "provider.not_configured"},
            },
        )
    ).json()

    deadline = asyncio.get_running_loop().time() + 20
    done: dict = {}
    while asyncio.get_running_loop().time() < deadline:
        done = (await alice.get(f"{T}/{created['id']}")).json()
        if done["status"] in ("succeeded", "failed", "cancelled"):
            break
        await asyncio.sleep(0.25)
    assert done["status"] == "failed"
    assert done["error_code"] == "provider.not_configured"
    assert done["actual_cost"] == 0

    after = await _balance(alice)
    # 任务终态和账务结算是两次提交（task.service.finish_execution）。
    # 先看到 failed 不代表 Worker 已完成随后的预扣释放，等待实际余额收敛。
    settlement_deadline = asyncio.get_running_loop().time() + 5
    while after["reserved"] != 0 and asyncio.get_running_loop().time() < settlement_deadline:
        await asyncio.sleep(0.1)
        after = await _balance(alice)
    assert after["reserved"] == 0, "预扣必须释放"
    assert after["total"] == before["total"], "没调上游，不该让用户买单"

    r = await alice.post(f"{T}/{created['id']}/retry")
    assert r.status_code == 409, "重试只会再失败一次，不该再预扣一笔"
