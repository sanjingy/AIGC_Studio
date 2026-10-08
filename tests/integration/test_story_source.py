"""`PUT /projects/{id}/source`：只保存故事原文，一分钱不花。

在这之前，写原文的唯一入口是 `advance`——存完立刻调 Router 扣钱。首页
「只创建项目」因此只能把原文丢掉，用户在故事页也没法先把稿子放进项目。

这里钉三件事：
1. **不花钱**：不建 AgentRun、不建 Task、不动账本与余额；
2. **不撒谎**：已有阶段产出、待处理的门、正在生成时拒绝替换，
   否则产出与原文对不上而界面看不出来；
3. **没原文不许开工**：routing 阶段没有原文时 `advance` 在调模型之前 422。
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.db import session_scope
from apps.api.modules.agent import repository as agent_repo
from apps.api.modules.agent import runner
from apps.api.modules.agent.models import AgentRun
from apps.api.modules.billing.models import CreditAccount, CreditTransaction
from apps.api.modules.project import service as project_service
from apps.api.modules.task.models import Task

pytestmark = pytest.mark.integration

P = "/api/v1/projects"

NOVEL = "雨夜，少女在旧车站捡到一封来自十年后的信。"


async def _project(client: AsyncClient) -> dict[str, Any]:
    resp = await client.post(P, json={"title": "原文保存"})
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


async def _put(client: AsyncClient, pid: str, text: str) -> Any:
    return await client.put(f"{P}/{pid}/source", json={"text": text})


async def _state(client: AsyncClient, pid: str) -> dict[str, Any]:
    resp = await client.get(f"{P}/{pid}/state")
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


async def _count(model: Any, project_id: str) -> int:
    async with session_scope() as db:
        return int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(model)
                    .where(model.project_id == uuid.UUID(project_id))
                )
            ).scalar_one()
        )


async def _ledger_count(org_id: str) -> int:
    async with session_scope() as db:
        return int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(CreditTransaction)
                    .join(CreditAccount, CreditAccount.id == CreditTransaction.account_id)
                    .where(CreditAccount.org_id == uuid.UUID(org_id))
                )
            ).scalar_one()
        )


async def _write_state(db: AsyncSession, project: dict[str, Any], state: dict[str, Any]) -> None:
    await project_service.update_current_state(
        db,
        org_id=uuid.UUID(project["org_id"]),
        project_id=uuid.UUID(project["id"]),
        state=state,
    )
    await db.commit()


# ------------------------------------------------------------------ 保存


async def test_put_source_saves_without_spending(alice: AsyncClient) -> None:
    project = await _project(alice)
    pid = project["id"]
    balance = (await alice.get("/api/v1/credits/balance")).json()
    ledger = await _ledger_count(project["org_id"])

    resp = await _put(alice, pid, f"  {NOVEL}\n")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"source": NOVEL, "chars": len(NOVEL), "stage": "routing", "changed": True}

    # 刷新之后读得回来：唯一权威是 current_state_json
    state = await _state(alice, pid)
    assert state["current_state_json"]["source"] == NOVEL
    assert state["stage"] == "routing"

    assert await _count(AgentRun, pid) == 0, "保存原文不该跑任何 Agent"
    assert await _count(Task, pid) == 0, "保存原文不该建任务"
    assert await _ledger_count(project["org_id"]) == ledger, "保存原文不该产生账本流水"
    assert (await alice.get("/api/v1/credits/balance")).json() == balance


async def test_put_same_source_is_idempotent(alice: AsyncClient) -> None:
    pid = (await _project(alice))["id"]
    assert (await _put(alice, pid, NOVEL)).json()["changed"] is True
    again = await _put(alice, pid, NOVEL)
    assert again.status_code == 200
    assert again.json()["changed"] is False

    replaced = await _put(alice, pid, "换一个创意：灯塔看守人的最后一夜。")
    assert replaced.json()["changed"] is True
    assert (await _state(alice, pid))["current_state_json"]["source"].startswith("换一个创意")


@pytest.mark.parametrize("text", ["", "   \n\t "])
async def test_blank_source_rejected(alice: AsyncClient, text: str) -> None:
    pid = (await _project(alice))["id"]
    resp = await _put(alice, pid, text)
    assert resp.status_code == 422, resp.text
    assert "source" not in (await _state(alice, pid))["current_state_json"]


async def test_over_limit_rejected_not_truncated(alice: AsyncClient) -> None:
    """`advance` 截断是历史行为；用户主动保存时悄悄丢后半本小说不可接受。"""
    pid = (await _project(alice))["id"]
    assert (await _put(alice, pid, "字" * 20_000)).status_code == 200
    resp = await _put(alice, pid, "字" * 20_001)
    assert resp.status_code == 422, resp.text
    assert len((await _state(alice, pid))["current_state_json"]["source"]) == 20_000


async def test_cross_tenant_is_404(alice: AsyncClient, bob: AsyncClient) -> None:
    pid = (await _project(alice))["id"]
    resp = await _put(bob, pid, NOVEL)
    assert resp.status_code == 404, resp.text
    assert "source" not in (await _state(alice, pid))["current_state_json"]


# ------------------------------------------------------------------ 什么时候不许换


async def test_locked_once_stage_output_exists(alice: AsyncClient, db: AsyncSession) -> None:
    project = await _project(alice)
    await _write_state(
        db, project, {"source": NOVEL, "stage": "await_plan", "plot_index": {"nodes": []}}
    )
    resp = await _put(alice, project["id"], "另一个故事")
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "agent.source.locked"
    assert (await _state(alice, project["id"]))["current_state_json"]["source"] == NOVEL


async def test_locked_for_legacy_outputs(alice: AsyncClient, db: AsyncSession) -> None:
    project = await _project(alice)
    await _write_state(db, project, {"source": NOVEL, "stage": "story", "story": {"x": 1}})
    resp = await _put(alice, project["id"], "另一个故事")
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "agent.source.locked"


async def test_locked_while_gate_pending(alice: AsyncClient, db: AsyncSession) -> None:
    project = await _project(alice)
    await agent_repo.create_approval(
        db,
        org_id=uuid.UUID(project["org_id"]),
        project_id=uuid.UUID(project["id"]),
        gate="plan",
        payload={"stage": "await_plan"},
    )
    await db.commit()
    resp = await _put(alice, project["id"], NOVEL)
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "agent.source.locked"


async def test_busy_while_agent_running(alice: AsyncClient, db: AsyncSession) -> None:
    project = await _project(alice)
    org_id, pid = uuid.UUID(project["org_id"]), uuid.UUID(project["id"])
    run = await agent_repo.create_run(
        db,
        org_id=org_id,
        project_id=pid,
        agent_id="router.default.v1",
        role="routing",
        input_json={},
    )
    await db.commit()
    resp = await _put(alice, project["id"], NOVEL)
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "agent.source.busy"

    # 被硬杀、永远停在 running 的旧运行不能把原文永久锁死
    run.created_at = datetime.now(UTC) - timedelta(hours=2)
    await db.commit()
    assert (await _put(alice, project["id"], NOVEL)).status_code == 200


async def test_new_source_discards_stale_route(alice: AsyncClient, db: AsyncSession) -> None:
    """Router 判过路线、情节目录还没跑：换原文要退回 routing 按新原文重判。"""
    project = await _project(alice)
    router_out = {"route": "NOVEL_TO_ANIME", "requires_clarification": False}
    await _write_state(db, project, {"source": NOVEL, "stage": "plot_index", "router": router_out})

    same = await _put(alice, project["id"], NOVEL)
    assert same.json() == {
        "source": NOVEL,
        "chars": len(NOVEL),
        "stage": "plot_index",
        "changed": False,
    }

    resp = await _put(alice, project["id"], "全新的故事")
    assert resp.status_code == 200, resp.text
    assert resp.json()["stage"] == "routing"
    state = (await _state(alice, project["id"]))["current_state_json"]
    assert "router" not in state
    assert state["source"] == "全新的故事"


# ------------------------------------------------------------------ advance


async def test_advance_without_source_refused_before_model(alice: AsyncClient) -> None:
    project = await _project(alice)
    ledger = await _ledger_count(project["org_id"])
    resp = await alice.post(f"{P}/{project['id']}/advance?to_gate=true", json={"user_input": "  "})
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "agent.source.required"
    assert await _count(AgentRun, project["id"]) == 0, "没原文就不该调 Router"
    assert await _ledger_count(project["org_id"]) == ledger


async def test_advance_uses_saved_source(alice: AsyncClient) -> None:
    """先免费保存，再不带输入推进：Router 和情节目录用的是保存的那份。"""
    pid = (await _project(alice))["id"]
    assert (await _put(alice, pid, NOVEL)).status_code == 200
    resp = await alice.post(f"{P}/{pid}/advance?to_gate=true", json={"user_input": ""})
    assert resp.status_code == 200, resp.text
    assert resp.json()["gate_opened"] == "plan"

    async with session_scope() as db:
        runs = (
            (await db.execute(select(AgentRun).where(AgentRun.project_id == uuid.UUID(pid))))
            .scalars()
            .all()
        )
    router = next(r for r in runs if r.agent_id == "router.default.v1")
    assert NOVEL in str(router.input_json)
    # 有了产出，原文就锁住了
    assert (await _put(alice, pid, "另一个")).status_code == 409


async def test_advance_input_after_routing_never_replaces_source(alice: AsyncClient) -> None:
    """过了 routing，advance 带的文字不能覆盖原文。

    此前任何阶段的 `user_input` 都会写进 `source`：界面上"补充说明"里的
    一句话就能把整篇小说换掉，而情节目录等产出仍挂在旧原文上。
    """
    pid = (await _project(alice))["id"]
    assert (await _put(alice, pid, NOVEL)).status_code == 200
    first = await alice.post(f"{P}/{pid}/advance?to_gate=true", json={"user_input": ""})
    assert first.status_code == 200, first.text
    # 门① 开着，advance 只会停在门口
    resp = await alice.post(
        f"{P}/{pid}/advance?to_gate=true", json={"user_input": "注意节奏要快一点"}
    )
    assert resp.status_code == 200, resp.text
    assert (await _state(alice, pid))["current_state_json"]["source"] == NOVEL


async def test_advance_cannot_overwrite_saved_source(alice: AsyncClient) -> None:
    """routing 阶段已经存了原文，推进带来另一份：409，且在调模型之前。

    `PUT /source` 的锁定检查（已有产出、待处理的门、正在生成）只在那条路上；
    让 advance 也能换原文，就是一条绕过它们的后门。旧页面、另一个标签页
    都会带着过时的原文来推进。
    """
    project = await _project(alice)
    pid = project["id"]
    assert (await _put(alice, pid, NOVEL)).status_code == 200
    ledger = await _ledger_count(project["org_id"])
    balance = (await alice.get("/api/v1/credits/balance")).json()

    resp = await alice.post(
        f"{P}/{pid}/advance?to_gate=true", json={"user_input": "旧标签页里的另一份原文"}
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "agent.source.conflict"
    state = await _state(alice, pid)
    assert state["current_state_json"]["source"] == NOVEL
    assert state["stage"] == "routing"
    assert await _count(AgentRun, pid) == 0, "冲突必须在调 Router 之前拒掉"
    assert await _count(Task, pid) == 0
    assert await _ledger_count(project["org_id"]) == ledger
    assert (await alice.get("/api/v1/credits/balance")).json() == balance


async def test_advance_with_same_source_passes(alice: AsyncClient) -> None:
    """带的是同一份原文（首页重试、首尾空白不同）照常推进，原文不变。"""
    pid = (await _project(alice))["id"]
    assert (await _put(alice, pid, NOVEL)).status_code == 200
    resp = await alice.post(f"{P}/{pid}/advance?to_gate=true", json={"user_input": f"\n{NOVEL}  "})
    assert resp.status_code == 200, resp.text
    assert resp.json()["gate_opened"] == "plan"
    assert (await _state(alice, pid))["current_state_json"]["source"] == NOVEL


async def test_first_advance_still_accepts_source(alice: AsyncClient) -> None:
    """没存过原文时，advance 仍是首次接纳原文的入口（首页「开始生产」的旧路径）。"""
    pid = (await _project(alice))["id"]
    resp = await alice.post(f"{P}/{pid}/advance?to_gate=true", json={"user_input": NOVEL})
    assert resp.status_code == 200, resp.text
    assert (await _state(alice, pid))["current_state_json"]["source"] == NOVEL


# ------------------------------------------------------------------ 推进与换原文的交错
#
# `advance` 不能把项目行锁跨过模型调用，所以"推进读状态 → 模型调用 → 写回"
# 这段时间里 `PUT /source` 可能插进来。以前写回是把调用前读到的整份 state
# 覆盖回去：新原文被旧原文顶掉，按旧原文生成的产出还挂在项目上。
#
# 交错用 Event 卡住、不靠 sleep 碰运气：
# - 卡在"读完状态、AgentRun 落库之前"（`get_lock_variables`）；
# - 卡在模型调用里（`runner.complete_structured`）。


class _Gate:
    """在被替换的函数里停住，直到测试放行。"""

    def __init__(self) -> None:
        self.reached = asyncio.Event()
        self.release = asyncio.Event()

    def wrap(self, fn: Any) -> Any:
        async def gated(*args: Any, **kwargs: Any) -> Any:
            self.reached.set()
            await self.release.wait()
            return await fn(*args, **kwargs)

        return gated


async def _wait(event: asyncio.Event) -> None:
    await asyncio.wait_for(event.wait(), timeout=15)


async def _runs(pid: str) -> list[AgentRun]:
    async with session_scope() as db:
        rows = await db.execute(select(AgentRun).where(AgentRun.project_id == uuid.UUID(pid)))
        return list(rows.scalars().all())


async def test_put_between_read_and_run_waits_then_is_busy(
    alice: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """推进读到原文、运行还没落库：PUT 等推进放锁，看到正在生成 → 409，原文不丢。

    这是此前会丢稿的窗口：读状态没锁，运行还没落库，PUT 判不出 busy 就换了原文，
    Router 跑完再把旧 state 整份写回。
    """
    project = await _project(alice)
    pid = project["id"]
    assert (await _put(alice, pid, NOVEL)).status_code == 200
    ledger = await _ledger_count(project["org_id"])

    before_run, in_model = _Gate(), _Gate()
    monkeypatch.setattr(
        project_service,
        "get_lock_variables",
        before_run.wrap(project_service.get_lock_variables),
    )
    monkeypatch.setattr(runner, "complete_structured", in_model.wrap(runner.complete_structured))

    adv = asyncio.create_task(
        alice.post(f"{P}/{pid}/advance?to_gate=false", json={"user_input": ""})
    )
    await _wait(before_run.reached)
    put = asyncio.create_task(_put(alice, pid, "新稿：灯塔看守人的最后一夜。"))
    await asyncio.sleep(0.5)
    assert not put.done(), "推进持有项目行锁时 PUT 必须等着，不能抢在运行落库之前换原文"

    before_run.release.set()
    await _wait(in_model.reached)  # 运行已落库（running），模型调用停在这里
    put_resp = await asyncio.wait_for(put, timeout=15)
    assert put_resp.status_code == 409, put_resp.text
    assert put_resp.json()["error"]["code"] == "agent.source.busy"

    in_model.release.set()
    adv_resp = await asyncio.wait_for(adv, timeout=30)
    assert adv_resp.status_code == 200, adv_resp.text

    state = (await _state(alice, pid))["current_state_json"]
    assert state["source"] == NOVEL
    assert "router" in state, "Router 按 NOVEL 跑的，产出照常落在 NOVEL 上"
    assert await _ledger_count(project["org_id"]) == ledger


async def test_source_replaced_during_model_call_discards_output(
    alice: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模型调用期间原文还是被换了（busy 判据有 15 分钟窗，被硬杀/超长的运行会过窗）：
    写回时发现原文变了，这一步作废——新原文不被覆盖，旧原文的产出不挂上去。

    用情节目录这一步：Router 已判过路线、还没有阶段产出，此时 PUT 是允许的。
    """
    project = await _project(alice)
    pid = project["id"]
    router_out = {"route": "NOVEL_TO_ANIME", "requires_clarification": False}
    async with session_scope() as db:
        await _write_state(
            db, project, {"source": NOVEL, "stage": "plot_index", "router": router_out}
        )
    ledger = await _ledger_count(project["org_id"])
    balance = (await alice.get("/api/v1/credits/balance")).json()

    in_model = _Gate()
    monkeypatch.setattr(runner, "complete_structured", in_model.wrap(runner.complete_structured))
    adv = asyncio.create_task(
        alice.post(f"{P}/{pid}/advance?to_gate=false", json={"user_input": ""})
    )
    await _wait(in_model.reached)

    # 让这次运行看起来过了 busy 窗，模拟 busy 判据失效时 PUT 照样换进来
    async with session_scope() as db:
        run = (
            await db.execute(select(AgentRun).where(AgentRun.project_id == uuid.UUID(pid)))
        ).scalar_one()
        assert run.status == "running"
        run.created_at = datetime.now(UTC) - timedelta(hours=2)
        await db.commit()
    new = "新稿：灯塔看守人的最后一夜。"
    put_resp = await _put(alice, pid, new)
    assert put_resp.status_code == 200, put_resp.text
    assert put_resp.json()["stage"] == "routing"

    in_model.release.set()
    adv_resp = await asyncio.wait_for(adv, timeout=30)
    assert adv_resp.status_code == 409, adv_resp.text
    assert adv_resp.json()["error"]["code"] == "agent.run.superseded"

    snap = await _state(alice, pid)
    state = snap["current_state_json"]
    assert state["source"] == new, "新原文不能被旧 state 覆盖"
    assert snap["stage"] == "routing"
    assert "plot_index" not in state, "按旧原文生成的情节目录不能挂到新原文上"
    assert "router" not in state

    (run,) = await _runs(pid)
    assert run.status == "failed"
    assert run.error_code == "agent.run.superseded"
    # 前端在 state 缺产出时会退回"最新一条带 output_json 的运行"，必须摘掉
    assert run.output_json is None
    resp = await alice.get(f"{P}/{pid}/agent-runs?limit=50")
    assert all(r["output_json"] is None for r in resp.json())

    # 同步推进这条路不走账本：作废不产生流水，也不需要退款
    assert await _ledger_count(project["org_id"]) == ledger
    assert (await alice.get("/api/v1/credits/balance")).json() == balance
    # 原文仍可继续编辑（作废的运行不算产出、也不再 running）
    assert (await _put(alice, pid, new + "补一句。")).status_code == 200


async def test_clarification_write_is_conditional_too(
    alice: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Router 要求澄清的那条写回同样是条件写：原文变了就不留旧路线。"""
    pid = (await _project(alice))["id"]
    assert (await _put(alice, pid, NOVEL)).status_code == 200

    in_model = _Gate()
    original = runner.complete_structured

    async def clarifying(**kwargs: Any) -> Any:
        completion = await original(**kwargs)
        out = completion.output.model_copy(update={"requires_clarification": True})
        return dataclasses.replace(completion, output=out)

    monkeypatch.setattr(runner, "complete_structured", in_model.wrap(clarifying))
    adv = asyncio.create_task(
        alice.post(f"{P}/{pid}/advance?to_gate=false", json={"user_input": ""})
    )
    await _wait(in_model.reached)
    async with session_scope() as db:
        run = (
            await db.execute(select(AgentRun).where(AgentRun.project_id == uuid.UUID(pid)))
        ).scalar_one()
        run.created_at = datetime.now(UTC) - timedelta(hours=2)
        await db.commit()
    assert (await _put(alice, pid, "新稿")).status_code == 200
    in_model.release.set()

    adv_resp = await asyncio.wait_for(adv, timeout=30)
    assert adv_resp.status_code == 409, adv_resp.text
    state = (await _state(alice, pid))["current_state_json"]
    assert state["source"] == "新稿"
    assert "router" not in state
