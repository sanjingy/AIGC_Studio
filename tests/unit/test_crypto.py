"""凭证加解密（ADR-025 / 09_Database.md §12）。

守的是"用户自带的 Key 落库后是不是真的看不见"。
用例里所有"密钥"都是假的占位串——真 Key 不进代码、不进测试固件。
"""

from __future__ import annotations

import base64

import pytest

from apps.api.core import config as config_module
from apps.api.core.crypto import CryptoConfigError, decrypt_secret, encrypt_secret

FAKE_KEY = "sk-fake-key-for-test-0123456789"


def test_round_trip() -> None:
    assert decrypt_secret(encrypt_secret(FAKE_KEY)) == FAKE_KEY


def test_round_trip_handles_unicode_and_empty() -> None:
    for plain in ("", "键-值-中文", "a" * 4096):
        assert decrypt_secret(encrypt_secret(plain)) == plain


def test_same_plaintext_encrypts_differently() -> None:
    """nonce 必须每次新随机。

    复用 nonce 不只是"密文长得一样"这么无害——GCM 下 nonce 复用可以
    反解出认证密钥，等于伪造能力白送。
    """
    assert encrypt_secret(FAKE_KEY) != encrypt_secret(FAKE_KEY)


def test_ciphertext_does_not_leak_plaintext() -> None:
    ct = encrypt_secret(FAKE_KEY)
    assert FAKE_KEY not in ct
    # base64 解码后同样不能出现明文片段——只做一层编码等于没加密
    assert FAKE_KEY.encode() not in base64.urlsafe_b64decode(ct)


def test_tampered_ciphertext_is_rejected() -> None:
    """被改过的密文必须抛错，不能悄悄返回一段垃圾。

    返回垃圾的后果是拿它去请求上游，得到一个无从归因的 401。
    """
    raw = bytearray(base64.urlsafe_b64decode(encrypt_secret(FAKE_KEY)))
    raw[-1] ^= 0x01
    with pytest.raises(CryptoConfigError):
        decrypt_secret(base64.urlsafe_b64encode(bytes(raw)).decode())


def test_garbage_input_is_rejected() -> None:
    with pytest.raises(CryptoConfigError):
        decrypt_secret("not-base64-!!!")
    with pytest.raises(CryptoConfigError):
        decrypt_secret(base64.urlsafe_b64encode(b"\x99short").decode())


def _with_env(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    config_module.get_settings.cache_clear()


def test_dedicated_key_is_used_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """配了专用密钥就该用它——换一把密钥，旧密文必须解不开。"""
    try:
        _with_env(
            monkeypatch,
            CREDENTIAL_ENCRYPTION_KEY=base64.b64encode(b"A" * 32).decode(),
        )
        ct = encrypt_secret(FAKE_KEY)
        assert decrypt_secret(ct) == FAKE_KEY

        _with_env(
            monkeypatch,
            CREDENTIAL_ENCRYPTION_KEY=base64.b64encode(b"B" * 32).decode(),
        )
        with pytest.raises(CryptoConfigError):
            decrypt_secret(ct)
    finally:
        config_module.get_settings.cache_clear()


def test_malformed_dedicated_key_is_rejected_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = "obviously-not-base64-material"
    try:
        _with_env(monkeypatch, CREDENTIAL_ENCRYPTION_KEY=bad)
        with pytest.raises(CryptoConfigError) as exc:
            encrypt_secret(FAKE_KEY)
        # 错误信息里出现密钥材料 = 把它写进了日志
        assert bad not in str(exc.value)
    finally:
        config_module.get_settings.cache_clear()


def test_wrong_length_dedicated_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    try:
        _with_env(monkeypatch, CREDENTIAL_ENCRYPTION_KEY=base64.b64encode(b"short").decode())
        with pytest.raises(CryptoConfigError):
            encrypt_secret(FAKE_KEY)
    finally:
        config_module.get_settings.cache_clear()


def test_production_refuses_to_derive_from_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """生产环境不许把用户 Key 的安全性挂在到处都在用的 SECRET_KEY 上。"""
    try:
        _with_env(
            monkeypatch,
            ENV="prod",
            SECRET_KEY="a-real-looking-production-secret",
            CREDENTIAL_ENCRYPTION_KEY="",
        )
        with pytest.raises(CryptoConfigError, match="CREDENTIAL_ENCRYPTION_KEY"):
            encrypt_secret(FAKE_KEY)
    finally:
        config_module.get_settings.cache_clear()
