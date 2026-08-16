from __future__ import annotations

from typing import Any

from apps.api.core.logging import _redact


def redact(d: dict[str, Any]) -> dict[str, Any]:
    return _redact(None, "", d)


def test_top_level_secret_is_redacted() -> None:
    out = redact({"event": "login", "password": "hunter2"})
    assert out["password"] == "***REDACTED***"
    assert out["event"] == "login"


def test_nested_secret_is_redacted() -> None:
    out = redact({"payload": {"provider": {"api_key": "sk-live-xxx"}}})
    assert out["payload"]["provider"]["api_key"] == "***REDACTED***"


def test_secret_inside_list_is_redacted() -> None:
    out = redact({"items": [{"token": "abc"}, {"safe": "ok"}]})
    assert out["items"][0]["token"] == "***REDACTED***"
    assert out["items"][1]["safe"] == "ok"


def test_key_matching_is_case_insensitive() -> None:
    out = redact({"Authorization": "Bearer x", "API_KEY": "y"})
    assert out["Authorization"] == "***REDACTED***"
    assert out["API_KEY"] == "***REDACTED***"


def test_deep_nesting_does_not_explode() -> None:
    deep: dict[str, Any] = {"password": "x"}
    for _ in range(50):
        deep = {"n": deep}
    redact(deep)  # 不抛异常即可
