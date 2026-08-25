""" "测试连接"的两条底线（ADR-025）。

1. 探测器在 `ENV=test` 下必须是 Mock——它会拿用户的**真 Key** 去打真实上游，
   漏一次就是把用户的凭据发出去了，比测试花钱更糟。
2. 上游回显的密钥材料必须抹干净。上游报错时把收到的 key 贴回来是常见做法，
   原样透出去等于把它写进浏览器控制台、前端日志和用户的截图。
"""

from __future__ import annotations

import asyncio

import pytest

from apps.api.core.errors import AppError
from apps.api.modules.gateway import probe

KEY = "sk-abcdefgh12345678ijklmnop"


def test_prober_is_mock_in_tests() -> None:
    probe.set_prober(None)  # 清缓存，走真实选择逻辑
    assert isinstance(probe.get_prober(), probe.MockProber), (
        "测试环境拿到了真实探测器，会把 Key 发给上游"
    )


def test_mock_is_deterministic() -> None:
    async def once(key: str) -> str:
        return await probe.MockProber().probe(provider_id="provider.deepseek", api_key=key)

    assert asyncio.run(once(KEY)) == asyncio.run(once(KEY))


def test_redacts_the_whole_key() -> None:
    out = probe.redact(f"Authentication Fails, your key {KEY} is invalid", KEY)
    assert KEY not in out
    assert "Authentication Fails" in out, "抹密钥不能把失败原因一起抹掉"


def test_redacts_a_truncated_echo() -> None:
    """上游常只回显前后几位。精确匹配挡不住，所以按片段扫。"""
    out = probe.redact(f"invalid key: {KEY[:16]}...", KEY)
    assert KEY[:16] not in out
    assert KEY[8:16] not in out


def test_redacts_a_key_we_did_not_submit() -> None:
    """错误文案里可能带着另一把 Key（比如上游把账号下别的 key 列出来）。"""
    out = probe.redact("conflicting key sk-OTHERKEY0987654321 already bound", "")
    assert "sk-OTHERKEY0987654321" not in out


def test_message_is_truncated() -> None:
    out = probe.redact("x" * 5000, KEY)
    assert len(out) == probe.MAX_MESSAGE_CHARS


def test_failed_probe_returns_a_result_not_an_error() -> None:
    """ "Key 不能用"是结论不是异常——抛成 4xx 会逼前端自己分辨网络错误。"""
    probe.set_prober(None)
    result = asyncio.run(probe.verify(provider_id="provider.dashscope", api_key="bad-key"))
    assert result.ok is False
    assert result.error_code == "provider.account.insufficient"
    assert "bad-key" not in result.message


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(AppError):
        probe._verifier("provider.nonexistent", KEY)
