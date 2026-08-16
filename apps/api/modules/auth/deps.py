"""FastAPI 依赖：从 Cookie 或 Authorization 头解析当前用户。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.db import get_db
from apps.api.core.errors import AppError
from apps.api.modules.auth import service
from apps.api.modules.auth.models import User

ACCESS_COOKIE = "aigc_access"
REFRESH_COOKIE = "aigc_refresh"


def read_access_token(request: Request) -> str:
    # 浏览器走 Cookie；脚本和将来的公网 API 走 Bearer 头。
    if token := request.cookies.get(ACCESS_COOKIE):
        return token

    auth = request.headers.get("Authorization", "")
    scheme, _, value = auth.partition(" ")
    if scheme.lower() == "bearer" and value:
        return value

    raise AppError("auth.token.invalid", message="missing credentials")


async def current_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    return await service.get_current_user(db, access_token=read_access_token(request))


CurrentUser = Annotated[User, Depends(current_user)]
DbSession = Annotated[AsyncSession, Depends(get_db)]
