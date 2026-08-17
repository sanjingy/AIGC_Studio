"""账号体系模块。

对外只暴露 service 层与 FastAPI 依赖。
其他模块 import models / repository 会被 ruff banned-api 拦下（ADR-009）。

注意：这里不导出 router。包的 __init__ 一旦 import router，就会把 router
依赖的 auth.deps 等一并拉进模块初始化链，任何 service 层的跨模块调用
都会撞循环导入。main.py 直接从 <module>.router 导入即可。
"""

from apps.api.modules.auth.deps import CurrentUser, DbSession, current_user
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
]
