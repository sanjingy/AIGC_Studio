"""密码哈希与 JWT 签发。

两条硬约束：
1. 密码用 argon2id，不用 bcrypt/sha。argon2 是当前密码哈希的标准答案。
2. 刷新令牌只存 SHA-256 摘要，明文只在响应里出现一次。
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from apps.api.core.config import get_settings
from apps.api.core.errors import AppError

_ALGO = "HS256"
_hasher = PasswordHasher()

TokenKind = Literal["access"]


def hash_password(raw: str) -> str:
    return _hasher.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    try:
        _hasher.verify(hashed, raw)
    except (VerifyMismatchError, InvalidHashError):
        return False
    return True


def needs_rehash(hashed: str) -> bool:
    """argon2 参数升级后，登录时顺带把老哈希升上来。"""
    try:
        return _hasher.check_needs_rehash(hashed)
    except InvalidHashError:
        return False


def create_access_token(*, user_id: uuid.UUID, org_id: uuid.UUID, role: str) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "org": str(org_id),
        "role": role,
        "typ": "access",
        "iat": now,
        "exp": now + timedelta(seconds=settings.access_token_ttl_seconds),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm=_ALGO)


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.secret_key.get_secret_value(),
            algorithms=[_ALGO],
            options={"require": ["exp", "sub", "typ"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AppError("auth.token.expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AppError("auth.token.invalid", message=str(exc)) from exc

    # 防止用 refresh token 冒充 access token
    if payload.get("typ") != "access":
        raise AppError("auth.token.invalid", message="token type mismatch")
    return payload


def new_refresh_token() -> tuple[str, str]:
    """返回 (明文, 摘要)。明文只回给客户端一次，库里只留摘要。"""
    raw = secrets.token_urlsafe(48)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    # 刷新令牌是 384 bit 的高熵随机串，不是用户口令，
    # 不需要慢哈希——SHA-256 足够且比对更快。
    return hashlib.sha256(raw.encode()).hexdigest()
