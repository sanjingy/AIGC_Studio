"""一段参考描述 → 一份正式角色档案，不需要先有项目。

守住四件事：

1. **不需要项目。** 用户手里先有角色、后有故事是常态；这条入口不碰
   projects，也不写 agent_runs（那张表的 project_id 是 NOT NULL）。
2. **产出与编排链路同构。** 走的是同一个 `visual.character.v1`、同一个
   CharacterSheets schema，所以前端能用同一个组件渲染。
3. **照常计费。** 真实 LLM 调用一律走 reserve → settle，差额退回。
   不计费的入口迟早被当成免费通道刷。
4. **跨租户 404。**

ENV=test 下 LLM 走 Mock（`llm.get_provider`），不打上游、不花真钱。
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

A = "/api/v1/assets"
C = "/api/v1/credits"

REFERENCE = (
    "一个三十出头的女法医，常年值夜班，说话很短。"
    "左眉有一道旧疤，习惯把头发全部束起来，穿洗得发白的深蓝工装。"
)


async def _generate(client: AsyncClient, description: str = REFERENCE, **extra: object) -> dict:
    r = await client.post(f"{A}/characters", json={"description": description, **extra})
    assert r.status_code == 201, r.text
    return dict(r.json())


async def _balance(client: AsyncClient) -> dict:
    return dict((await client.get(f"{C}/balance")).json())


# ------------------------------------------------------------------ 主路径


async def test_generates_without_any_project(alice: AsyncClient) -> None:
    """全程不建项目。这正是这条入口存在的理由。"""
    assert (await alice.get("/api/v1/projects")).json()["items"] == []

    body = await _generate(alice)
    entry = body["entry"]

    assert entry["agent_id"] == "visual.character.v1"
    assert entry["source_text"] == REFERENCE
    assert entry["output"]["characters"], "产出必须是 CharacterSheets"
    # 标题取自产出里的角色名，用户在列表里认得出来
    assert entry["title"]

    # 依然没有项目被创建
    assert (await alice.get("/api/v1/projects")).json()["items"] == []


async def test_entry_appears_in_library(alice: AsyncClient) -> None:
    body = await _generate(alice)
    library = (await alice.get(f"{A}/library")).json()

    ids = [c["id"] for c in library["characters"]]
    assert body["entry"]["id"] in ids
    # 独立角色档案不挂项目，所以不该混进按项目分组的 profiles 里
    assert library["profiles"] == []


async def test_output_matches_the_project_pipeline_schema(alice: AsyncClient) -> None:
    """产出必须能被 CharacterSheets 校验——前端用同一个组件渲染两处产出，
    schema 一分叉，其中一处就会白屏。"""
    from agents.schemas import resolve

    entry = (await _generate(alice))["entry"]
    resolve("CharacterSheets").model_validate(entry["output"])


async def test_can_be_filed_into_a_folder_on_creation(alice: AsyncClient) -> None:
    folder = (await alice.post(f"{A}/folders", json={"name": "角色参考"})).json()
    body = await _generate(alice, folder_id=folder["id"])
    assert body["entry"]["folder_id"] == folder["id"]

    view = (await alice.get(f"{A}/library?folder_id={folder['id']}")).json()
    assert [c["id"] for c in view["characters"]] == [body["entry"]["id"]]


async def test_delete_entry(alice: AsyncClient) -> None:
    entry_id = (await _generate(alice))["entry"]["id"]
    assert (await alice.delete(f"{A}/characters/{entry_id}")).status_code == 204
    assert (await alice.get(f"{A}/characters/{entry_id}")).status_code == 404
    assert (await alice.get(f"{A}/library")).json()["characters"] == []


# ------------------------------------------------------------------ 计费


async def test_reserves_and_settles_credits(alice: AsyncClient) -> None:
    """预扣 → 结算 → 差额退回，一次都不能少。

    Mock 模型没有定价（这是刻意的：结算拿不准时不向用户收钱，见
    `pricing.text_run_cost`），所以实际成本是 0、预扣全额退回。
    要断言的是**流水的形状**，不是那个具体数字——数字来自
    model_pricing / pricing_rules，写死在测试里就等于把价格写进了代码。
    """
    before = await _balance(alice)
    body = await _generate(alice)

    assert body["reserved_credits"] > 0, "真实 LLM 调用必须预扣"
    assert body["cost_credits"] <= body["reserved_credits"], "结算不得超过预扣"

    after = await _balance(alice)
    assert after["reserved"] == before["reserved"], "结算后不该有钱挂在 reserved 里"
    assert after["balance"] == before["balance"] - body["cost_credits"]

    # 流水里两条都在，且挂在这条档案上而不是一个不存在的 task 上
    txs = (await alice.get(f"{C}/transactions")).json()
    mine = [t for t in txs if t["ref_id"] == body["entry"]["id"]]
    assert {t["type"] for t in mine} == {"RESERVE", "CONSUME"}
    assert {t["ref_type"] for t in mine} == {"character_entry"}


async def test_small_text_run_still_costs_at_least_one_credit(db: AsyncSession) -> None:
    """不足 1 Credit 的调用按 1 收，不向下取整。

    2026-08-19 用真实 deepseek-chat 跑通这条入口时发现：一次角色档案约 600 个
    输出 token，按每百万 400 Credits 算是 0.24，向下取整之后**结算金额是 0**。
    那不是"便宜"，是每一次文本调用都白干。
    """
    from apps.api.modules.billing import pricing

    # 模型 id 用种子数据里真实存在的那个；具体价格仍然来自 model_pricing 表，
    # 这里只断言"不会收 0"，不断言金额，否则等于把价格写进了测试。
    charged = await pricing.text_run_cost(db, model_id="deepseek-chat", tokens_out=600)
    assert charged >= 1

    # 一个 token 都没产出的调用不收钱
    assert await pricing.text_run_cost(db, model_id="deepseek-chat", tokens_out=0) == 0


async def test_insufficient_credits_blocks_generation(alice: AsyncClient) -> None:
    """余额不够时在调模型**之前**就被拦下：跑完再扣的话钱已经花在上游了。"""
    from apps.api.core.db import get_session_factory
    from apps.api.modules.billing import repository as billing_repo

    me = (await alice.get("/api/v1/auth/me")).json()
    async with get_session_factory()() as s:
        acc = await billing_repo.lock_account(s, org_id=uuid.UUID(me["org_id"]))
        await billing_repo.append(
            s,
            account=acc,
            tx_type="ADJUST",
            amount=-acc.balance,
            reserved_delta=0,
            idempotency_key=f"test-drain:{uuid.uuid4()}",
            note="清空余额",
        )
        await s.commit()

    r = await alice.post(f"{A}/characters", json={"description": REFERENCE})
    assert r.status_code == 402, r.text
    assert r.json()["error"]["code"] == "billing.credit.insufficient"
    # 失败不留半份产出
    assert (await alice.get(f"{A}/library")).json()["characters"] == []


# ------------------------------------------------------------------ 输入校验与隔离


async def test_empty_description_rejected(alice: AsyncClient) -> None:
    assert (await alice.post(f"{A}/characters", json={"description": "   "})).status_code == 422


async def test_overlong_description_rejected(alice: AsyncClient) -> None:
    r = await alice.post(f"{A}/characters", json={"description": "描" * 6001})
    assert r.status_code == 422, r.text


async def test_cross_tenant_entry_returns_404(alice: AsyncClient, bob: AsyncClient) -> None:
    entry_id = (await _generate(alice))["entry"]["id"]

    assert (await bob.get(f"{A}/characters/{entry_id}")).status_code == 404
    assert (await bob.delete(f"{A}/characters/{entry_id}")).status_code == 404
    assert (await bob.get(f"{A}/library")).json()["characters"] == []


async def test_requires_auth(client: AsyncClient) -> None:
    assert (
        await client.post(f"{A}/characters", json={"description": REFERENCE})
    ).status_code == 401
