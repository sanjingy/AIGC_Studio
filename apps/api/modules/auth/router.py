from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from apps.api.core.config import get_settings
from apps.api.modules.auth import service
from apps.api.modules.auth.deps import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    CurrentUser,
    DbSession,
)
from apps.api.modules.auth.schemas import LoginIn, RegisterIn, SessionOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

# 刷新令牌只在刷新和登出这两个端点用得到，限制路径可以缩小暴露面。
_REFRESH_PATH = "/api/v1/auth"


def _set_session_cookies(response: Response, session: service.IssuedSession) -> None:
    secure = get_settings().is_production
    # httpOnly：JS 读不到，XSS 也偷不走。
    # SameSite=lax：前端经 Next.js rewrite 代理，与 API 同源，够用且能防 CSRF。
    response.set_cookie(
        ACCESS_COOKIE,
        session.access_token,
        max_age=session.access_ttl,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        session.refresh_token,
        max_age=session.refresh_ttl,
        httponly=True,
        secure=secure,
        samesite="lax",
        path=_REFRESH_PATH,
    )


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path=_REFRESH_PATH)


@router.post("/register", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterIn, request: Request, response: Response, db: DbSession
) -> SessionOut:
    session = await service.register(
        db,
        email=payload.email,
        password=payload.password,
        display_name=payload.display_name,
        user_agent=request.headers.get("User-Agent"),
    )
    _set_session_cookies(response, session)
    return SessionOut(
        user=UserOut.model_validate(session.user), access_expires_in=session.access_ttl
    )


@router.post("/login", response_model=SessionOut)
async def login(
    payload: LoginIn, request: Request, response: Response, db: DbSession
) -> SessionOut:
    session = await service.login(
        db,
        email=payload.email,
        password=payload.password,
        user_agent=request.headers.get("User-Agent"),
    )
    _set_session_cookies(response, session)
    return SessionOut(
        user=UserOut.model_validate(session.user), access_expires_in=session.access_ttl
    )


@router.post("/refresh", response_model=SessionOut)
async def refresh(request: Request, response: Response, db: DbSession) -> SessionOut:
    session = await service.refresh(
        db,
        raw_token=request.cookies.get(REFRESH_COOKIE, ""),
        user_agent=request.headers.get("User-Agent"),
    )
    _set_session_cookies(response, session)
    return SessionOut(
        user=UserOut.model_validate(session.user), access_expires_in=session.access_ttl
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, db: DbSession) -> None:
    await service.logout(db, raw_token=request.cookies.get(REFRESH_COOKIE))
    _clear_session_cookies(response)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
