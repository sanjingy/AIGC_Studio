"""账号体系的唯一对外入口。

其他模块只能 import 这里的函数，不能碰 repository / models
（ruff banned-api 会拦，见 ADR-009）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.config import get_settings
from apps.api.core.errors import AppError
from apps.api.core.logging import get_logger
from apps.api.modules.auth import repository as repo
from apps.api.modules.auth import security
from apps.api.modules.auth.models import User
from apps.api.modules.billing import service as billing

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class IssuedSession:
    user: User
    access_token: str
    refresh_token: str
    access_ttl: int
    refresh_ttl: int


async def register(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    display_name: str,
    user_agent: str | None = None,
) -> IssuedSession:
    if await repo.get_user_by_email(db, email) is not None:
        raise AppError("auth.email.taken")

    user = await repo.create_org_with_owner(
        db,
        email=email,
        password_hash=security.hash_password(password),
        display_name=display_name,
    )

    try:
        session = await _issue(db, user, user_agent=user_agent)
        await db.commit()
    except IntegrityError as exc:
        # 并发注册同一邮箱：两个请求都通过了上面的存在性检查，
        # 靠数据库的部分唯一索引兜底。
        await db.rollback()
        raise AppError("auth.email.taken") from exc

    # 发放注册体验额度。放在会话签发之后：即便发放失败也不该
    # 阻断注册——用户拿不到额度是可修复的，注册失败不是。
    try:
        granted = await billing.grant_welcome_credits(db, org_id=user.org_id)
    except Exception as exc:
        granted = 0
        log.warning("auth.welcome_grant_failed", org_id=str(user.org_id), error=repr(exc))

    log.info(
        "auth.registered",
        user_id=str(user.id),
        org_id=str(user.org_id),
        welcome_credits=granted,
    )
    return session


async def login(
    db: AsyncSession, *, email: str, password: str, user_agent: str | None = None
) -> IssuedSession:
    user = await repo.get_user_by_email(db, email)

    # 邮箱不存在时也要走一次哈希校验，否则响应时间差会暴露
    # "这个邮箱是否注册过"。
    stored = user.password_hash if user else _DUMMY_HASH
    ok = security.verify_password(password, stored)

    if user is None or not ok:
        raise AppError("auth.credentials.invalid")
    if user.status != "active":
        raise AppError("auth.credentials.invalid", message=f"user status={user.status}")

    if security.needs_rehash(user.password_hash):
        await repo.update_password_hash(db, user, security.hash_password(password))

    await repo.touch_last_login(db, user)
    session = await _issue(db, user, user_agent=user_agent)
    await db.commit()

    log.info("auth.login", user_id=str(user.id))
    return session


async def refresh(
    db: AsyncSession, *, raw_token: str, user_agent: str | None = None
) -> IssuedSession:
    token_hash = security.hash_refresh_token(raw_token)
    row = await repo.get_refresh_token(db, token_hash)

    if row is None:
        raise AppError("auth.token.invalid", message="refresh token not found")

    if row.revoked_at is not None:
        # 已撤销的令牌又被用了 —— 说明它被复制过。
        # 无法判断是攻击者还是真用户，所以整族失效，强制全部重新登录。
        revoked = await repo.revoke_family(db, row.family_id)
        await db.commit()
        log.warning(
            "auth.refresh_token_reuse",
            user_id=str(row.user_id),
            family_id=str(row.family_id),
            revoked_count=revoked,
        )
        raise AppError("auth.token.invalid", message="refresh token reuse detected")

    if row.expires_at <= datetime.now(UTC):
        raise AppError("auth.token.expired")

    user = await repo.get_user_by_id(db, row.user_id)
    if user is None or user.status != "active":
        raise AppError("auth.token.invalid", message="user unavailable")

    # 轮换：签发新令牌，旧的立刻作废并指向新的，保留链路便于溯源。
    session = await _issue(db, user, family_id=row.family_id, user_agent=user_agent)
    new_row = await repo.get_refresh_token(db, security.hash_refresh_token(session.refresh_token))
    await repo.revoke_token(db, row, replaced_by=new_row.id if new_row else None)
    await db.commit()
    return session


async def logout(db: AsyncSession, *, raw_token: str | None) -> None:
    if not raw_token:
        return
    row = await repo.get_refresh_token(db, security.hash_refresh_token(raw_token))
    if row is not None and row.revoked_at is None:
        # 登出撤整族：用户点"退出"的预期是这台设备的会话彻底结束，
        # 而不是"当前这个令牌失效但上一个还能用"。
        await repo.revoke_family(db, row.family_id)
        await db.commit()


async def get_current_user(db: AsyncSession, *, access_token: str) -> User:
    payload = security.decode_access_token(access_token)
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise AppError("auth.token.invalid", message="bad subject") from exc

    user = await repo.get_user_by_id(db, user_id)
    if user is None or user.status != "active":
        raise AppError("auth.token.invalid", message="user unavailable")
    return user


# ---------------------------------------------------------------- 内部


async def _issue(
    db: AsyncSession,
    user: User,
    *,
    family_id: uuid.UUID | None = None,
    user_agent: str | None = None,
) -> IssuedSession:
    settings = get_settings()
    raw, digest = security.new_refresh_token()

    await repo.insert_refresh_token(
        db,
        user_id=user.id,
        family_id=family_id or uuid.uuid4(),
        token_hash=digest,
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.refresh_token_ttl_seconds),
        user_agent=user_agent,
    )

    return IssuedSession(
        user=user,
        access_token=security.create_access_token(
            user_id=user.id, org_id=user.org_id, role=user.role
        ),
        refresh_token=raw,
        access_ttl=settings.access_token_ttl_seconds,
        refresh_ttl=settings.refresh_token_ttl_seconds,
    )


# 用于登录时的恒定时间比对，避免通过响应时间枚举已注册邮箱。
_DUMMY_HASH = security.hash_password("dummy-password-for-timing-equalization")
