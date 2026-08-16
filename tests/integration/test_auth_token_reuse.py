"""刷新令牌轮换与重放检测。

这是整个账号体系里最容易写错、后果最严重的一块：
令牌被复制后如果不做检测，攻击者可以无限续期，且用户完全无感。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.modules.auth.deps import REFRESH_COOKIE
from apps.api.modules.auth.models import RefreshToken
from apps.api.modules.auth.security import hash_refresh_token

pytestmark = pytest.mark.integration

BASE = "/api/v1/auth"


async def test_reused_token_revokes_whole_family(
    client: AsyncClient, db: AsyncSession, credentials: dict[str, str]
) -> None:
    await client.post(f"{BASE}/register", json=credentials)
    stolen = client.cookies[REFRESH_COOKIE]

    # 真用户正常刷新一次，stolen 随之作废
    assert (await client.post(f"{BASE}/refresh")).status_code == 200
    fresh = client.cookies[REFRESH_COOKIE]
    assert fresh != stolen

    # 攻击者拿着复制来的旧令牌再刷一次
    client.cookies.set(REFRESH_COOKIE, stolen)
    replay = await client.post(f"{BASE}/refresh")
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "auth.token.invalid"

    # 关键：真用户手上那个"合法"令牌也必须一并失效。
    # 因为无法分辨谁是真身，只能全族强制重新登录。
    client.cookies.set(REFRESH_COOKIE, fresh)
    after = await client.post(f"{BASE}/refresh")
    assert after.status_code == 401

    family = (
        await db.execute(
            select(func.count())
            .select_from(RefreshToken)
            .where(
                RefreshToken.token_hash.in_(
                    [hash_refresh_token(stolen), hash_refresh_token(fresh)]
                ),
                RefreshToken.revoked_at.is_(None),
            )
        )
    ).scalar_one()
    assert family == 0, "重放后整族都应处于已撤销状态"


async def test_refresh_chain_is_traceable(
    client: AsyncClient, db: AsyncSession, credentials: dict[str, str]
) -> None:
    """旧令牌要指向替换它的新令牌，出事时能顺着链路溯源。"""
    await client.post(f"{BASE}/register", json=credentials)
    first = client.cookies[REFRESH_COOKIE]

    await client.post(f"{BASE}/refresh")
    second = client.cookies[REFRESH_COOKIE]

    old = (
        await db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(first))
        )
    ).scalar_one()
    new = (
        await db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(second))
        )
    ).scalar_one()

    assert old.revoked_at is not None
    assert old.replaced_by == new.id
    assert old.family_id == new.family_id, "轮换必须留在同一族内"
    assert new.revoked_at is None


async def test_refresh_token_stored_only_as_hash(
    client: AsyncClient, db: AsyncSession, credentials: dict[str, str]
) -> None:
    """库里不能出现明文令牌——库被拖走不能直接拿来登录。"""
    await client.post(f"{BASE}/register", json=credentials)
    raw = client.cookies[REFRESH_COOKIE]

    hit = (
        await db.execute(select(RefreshToken).where(RefreshToken.token_hash == raw))
    ).scalar_one_or_none()
    assert hit is None, "明文令牌不应出现在库中"

    row = (
        await db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw))
        )
    ).scalar_one()
    assert len(row.token_hash) == 64


async def test_unknown_refresh_token_rejected(client: AsyncClient) -> None:
    client.cookies.set(REFRESH_COOKIE, "not-a-real-token")
    assert (await client.post(f"{BASE}/refresh")).status_code == 401


async def test_access_token_cannot_be_used_as_refresh(
    client: AsyncClient, credentials: dict[str, str]
) -> None:
    from apps.api.modules.auth.deps import ACCESS_COOKIE

    await client.post(f"{BASE}/register", json=credentials)
    access = client.cookies[ACCESS_COOKIE]

    client.cookies.set(REFRESH_COOKIE, access)
    assert (await client.post(f"{BASE}/refresh")).status_code == 401
