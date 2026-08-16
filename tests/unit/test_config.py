from __future__ import annotations

import pytest

from apps.api.core.config import Settings


def test_database_url_is_async_driver() -> None:
    s = Settings(postgres_host="db", postgres_db="x")
    assert s.database_url.startswith("postgresql+asyncpg://")
    assert "/x" in s.database_url


def test_secrets_are_not_reprd() -> None:
    """配置对象被日志打印时不能泄露密码。

    坑点：SecretStr 只保护字段本身，但 database_url 是 computed_field，
    默认会进 repr()，而连接串里是明文密码。必须 repr=False。
    """
    s = Settings(postgres_password="super-secret-value")  # type: ignore[arg-type]
    assert "super-secret-value" not in repr(s)
    assert "super-secret-value" not in str(s)


def test_computed_urls_stay_usable() -> None:
    """repr=False 只影响打印，取值必须照常。"""
    s = Settings(postgres_password="pw", postgres_host="h")  # type: ignore[arg-type]
    assert "pw" in s.database_url
    assert s.redis_url.startswith("redis://")


def test_production_rejects_default_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """生产环境用默认密钥启动 = 灾难。必须拒绝启动。"""
    from apps.api.core import config as config_module

    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("SECRET_KEY", "dev-only-insecure-key-please-change")
    config_module.get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="默认 SECRET_KEY"):
            config_module.get_settings()
    finally:
        config_module.get_settings.cache_clear()
