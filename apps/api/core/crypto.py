"""凭证密文字段的加解密（09_Database.md §12 / ADR-025）。

只服务一件事：把用户自带的上游 API Key（BYOK）以密文形式落库，
用的时候在内存里解开，用完即弃。

**算法**：AES-256-GCM。选它而不是 pgcrypto，是因为密钥不需要交给
数据库——DBA、备份文件、逻辑复制流里都只有密文，泄露一份 dump
不等于泄露 Key。GCM 自带完整性校验，被人改过的密文解密时直接抛错，
不会悄悄返回一段垃圾去请求上游。

**密文格式**：``base64url(version || nonce || ciphertext||tag)``
版本号一个字节，同时作为 GCM 的 associated data 参与认证——
以后换算法或换密钥体系时，旧密文能被识别出来，且没法被降级篡改。

**这个模块的所有异常都不带密钥、明文或密文内容**。
错误信息里出现任何一段材料，都等于把它写进了日志。
"""

from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from apps.api.core.config import get_settings

_KEY_BYTES = 32  # AES-256
_NONCE_BYTES = 12  # GCM 标准 nonce 长度
_VERSION = b"\x01"

# HKDF 的 info：把派生密钥绑定到用途上。同一个 SECRET_KEY 以后再派生别的
# 用途密钥时，换一个 info 就是互不相干的两把钥匙。
_HKDF_INFO = b"aigc-studio/provider-credential/v1"


class CryptoConfigError(RuntimeError):
    """加密密钥缺失或格式不对。信息里不含任何密钥材料。"""


def _encryption_key() -> bytes:
    """取 32 字节对称密钥。

    优先用专用的 ``CREDENTIAL_ENCRYPTION_KEY``（base64 编码的 32 字节）：
    它与签发 JWT 的 ``SECRET_KEY`` 是两把钥匙，泄露其一不牵连另一个，
    也能各自独立轮换——轮换 SECRET_KEY 只是让所有人重新登录，
    轮换加密密钥却要重新加密整张表，两者的节奏本来就不该绑在一起。

    没配专用密钥时（本地开发、测试）从 ``SECRET_KEY`` HKDF 派生，
    保证开箱即用；但**生产环境拒绝这条退路**——那意味着 Key 的安全性
    完全挂在一个到处都在用的值上。
    """
    settings = get_settings()
    raw = settings.credential_encryption_key.get_secret_value().strip()

    if raw:
        try:
            key = base64.b64decode(raw, validate=True)
        except Exception:
            # `from None` 不可省：原异常的 args 里可能带着被解码的片段，
            # 链上去就等于把密钥材料写进了 traceback。
            raise CryptoConfigError(
                "CREDENTIAL_ENCRYPTION_KEY 不是合法 base64，应为 base64 编码的 32 字节随机值"
            ) from None
        if len(key) != _KEY_BYTES:
            raise CryptoConfigError(f"CREDENTIAL_ENCRYPTION_KEY 解码后必须是 {_KEY_BYTES} 字节")
        return key

    if settings.is_production:
        raise CryptoConfigError(
            "生产环境必须配置 CREDENTIAL_ENCRYPTION_KEY，拒绝从 SECRET_KEY 派生"
        )

    return HKDF(
        algorithm=SHA256(),
        length=_KEY_BYTES,
        salt=None,
        info=_HKDF_INFO,
    ).derive(settings.secret_key.get_secret_value().encode("utf-8"))


def encrypt_secret(plaintext: str) -> str:
    """加密一段明文密钥，返回可直接入库的 base64url 密文。

    每次调用都用新的随机 nonce，因此同样的明文两次加密结果不同——
    这不只是好看：nonce 复用会让 GCM 的认证密钥被算出来。
    """
    nonce = os.urandom(_NONCE_BYTES)
    blob = AESGCM(_encryption_key()).encrypt(
        nonce, plaintext.encode("utf-8"), associated_data=_VERSION
    )
    return base64.urlsafe_b64encode(_VERSION + nonce + blob).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    """解密 :func:`encrypt_secret` 的产物。

    密文被改过、密钥换了、版本对不上，一律抛 :class:`CryptoConfigError`
    而不是返回垃圾——拿一段垃圾去请求上游只会得到一个难以归因的 401。
    """
    try:
        raw = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
    except Exception:
        raise CryptoConfigError("密文不是合法 base64url") from None

    if len(raw) < len(_VERSION) + _NONCE_BYTES or raw[: len(_VERSION)] != _VERSION:
        raise CryptoConfigError("密文格式或版本不受支持")

    nonce = raw[len(_VERSION) : len(_VERSION) + _NONCE_BYTES]
    body = raw[len(_VERSION) + _NONCE_BYTES :]
    try:
        plain = AESGCM(_encryption_key()).decrypt(nonce, body, associated_data=_VERSION)
    except InvalidTag:
        raise CryptoConfigError("密文校验失败：已被篡改，或与当前加密密钥不匹配") from None
    return plain.decode("utf-8")
