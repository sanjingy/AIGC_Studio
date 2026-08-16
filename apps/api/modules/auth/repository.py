"""数据访问。模块私有——跨模块访问请走 service。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.modules.auth.models import (
    Organization,
    RefreshToken,
    User,
    UserPreference,
)


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    stmt = select(User).where(User.email == email, User.deleted_at.is_(None))
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    stmt = select(User).where(User.id == user_id, User.deleted_at.is_(None))
    return (await db.execute(stmt)).scalar_one_or_none()


async def create_org_with_owner(
    db: AsyncSession, *, email: str, password_hash: str, display_name: str
) -> User:
    org = Organization(name=f"{display_name} 的工作区", kind="personal")
    db.add(org)
    await db.flush()  # 拿到 org.id

    user = User(
        org_id=org.id,
        email=email,
        password_hash=password_hash,
        display_name=display_name,
        role="owner",
    )
    db.add(user)
    await db.flush()  # 拿到 user.id

    org.owner_user_id = user.id
    db.add(UserPreference(user_id=user.id))
    await db.flush()
    return user


async def touch_last_login(db: AsyncSession, user: User) -> None:
    user.last_login_at = datetime.now(UTC)


async def update_password_hash(db: AsyncSession, user: User, password_hash: str) -> None:
    user.password_hash = password_hash


# ------------------------------------------------------------- refresh token


async def insert_refresh_token(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    family_id: uuid.UUID,
    token_hash: str,
    expires_at: datetime,
    user_agent: str | None,
) -> RefreshToken:
    row = RefreshToken(
        user_id=user_id,
        family_id=family_id,
        token_hash=token_hash,
        expires_at=expires_at,
        user_agent=(user_agent or "")[:256] or None,
    )
    db.add(row)
    await db.flush()
    return row


async def get_refresh_token(db: AsyncSession, token_hash: str) -> RefreshToken | None:
    stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    return (await db.execute(stmt)).scalar_one_or_none()


async def revoke_token(
    db: AsyncSession, token: RefreshToken, *, replaced_by: uuid.UUID | None = None
) -> None:
    token.revoked_at = datetime.now(UTC)
    token.replaced_by = replaced_by


async def revoke_family(db: AsyncSession, family_id: uuid.UUID) -> int:
    """令牌重放时整族失效。返回受影响行数。"""
    stmt = (
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    result = await db.execute(stmt)
    # rowcount 存在于 CursorResult 上，Result 的类型标注里没有
    return cast("CursorResult[Any]", result).rowcount or 0
