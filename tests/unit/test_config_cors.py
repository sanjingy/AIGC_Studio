"""CORS 配置解析。

这些用例必须走**环境变量**而不是构造函数传参。
pydantic-settings 对 list 字段的 JSON 解析发生在读取环境变量的源层，
早于 field_validator；只用构造函数传参测试会完全绕过这条路径，
让 `CORS_ORIGINS=a,b` 这种最常见的写法在启动时才炸。
"""

from __future__ import annotations

import pytest

from apps.api.core.config import Settings


def test_env_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://a.com, http://b.com")
    assert Settings().cors_origins == ["http://a.com", "http://b.com"]


def test_env_single_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://a.com")
    assert Settings().cors_origins == ["http://a.com"]


def test_env_json_array_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", '["http://a.com","http://b.com"]')
    assert Settings().cors_origins == ["http://a.com", "http://b.com"]


def test_env_ignores_empty_segments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://a.com,,")
    assert Settings().cors_origins == ["http://a.com"]


def test_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert Settings(_env_file=None).cors_origins == ["http://localhost:3000"]


def test_native_list_via_kwargs() -> None:
    assert Settings(cors_origins=["http://a.com"]).cors_origins == ["http://a.com"]
