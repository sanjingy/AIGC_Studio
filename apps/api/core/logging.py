"""结构化日志 + 请求追踪。

关键约束（05_ModelRuntimeSpec.md 第 7 节 / 09_Database.md 第 12 节）：
API Key、密码、token 等敏感字段绝不能进日志。这里用一个处理器做兜底脱敏，
但兜底不是免责——业务代码本来就不该把这些东西传进日志。
"""

from __future__ import annotations

import logging
import sys
import uuid
from contextvars import ContextVar
from typing import Any

import structlog
from structlog.typing import EventDict

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")

SENSITIVE_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "secret",
        "secret_key",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "authorization",
        "node_secret",
        "private_key",
        "encrypted_token",
    }
)

_REDACTED = "***REDACTED***"


def _redact(_logger: Any, _name: str, event_dict: EventDict) -> EventDict:
    def scrub(value: Any, depth: int = 0) -> Any:
        if depth > 6:  # 防御深层嵌套导致的栈问题
            return value
        if isinstance(value, dict):
            return {
                k: (_REDACTED if str(k).lower() in SENSITIVE_KEYS else scrub(v, depth + 1))
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [scrub(v, depth + 1) for v in value]
        return value

    return dict(scrub(dict(event_dict)))


def _add_trace_id(_logger: Any, _name: str, event_dict: EventDict) -> EventDict:
    if tid := trace_id_var.get():
        event_dict["trace_id"] = tid
    return event_dict


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _add_trace_id,
            _redact,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def new_trace_id() -> str:
    return uuid.uuid4().hex


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
