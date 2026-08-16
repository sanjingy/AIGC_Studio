"""账号体系模块。

对外只暴露 service 层与 FastAPI 依赖。
其他模块 import models / repository 会被 ruff banned-api 拦下（ADR-009）。
"""

from apps.api.modules.auth.deps import CurrentUser, DbSession, current_user
from apps.api.modules.auth.router import router
from apps.api.modules.auth.service import (
    get_current_user,
    login,
    logout,
    refresh,
    register,
)

__all__ = [
    "CurrentUser",
    "DbSession",
    "current_user",
    "get_current_user",
    "login",
    "logout",
    "refresh",
    "register",
    "router",
]
