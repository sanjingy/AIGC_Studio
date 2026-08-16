from __future__ import annotations

import pytest

from apps.api.core.errors import ERRORS, AppError, Disposition


def test_unknown_code_fails_loudly() -> None:
    """未登记的错误码必须在开发期就炸出来，而不是悄悄返回一个 500。"""
    with pytest.raises(RuntimeError, match="未登记的错误码"):
        AppError("nope.not.registered")


def test_code_matches_key() -> None:
    for key, spec in ERRORS.items():
        assert key == spec.code, f"ERRORS 的 key 与 spec.code 不一致：{key}"


def test_code_format() -> None:
    """错误码必须是 <domain>.<category>[.<specific>] 形式。"""
    for code in ERRORS:
        parts = code.split(".")
        assert 2 <= len(parts) <= 3, f"错误码格式不合规：{code}"
        assert all(p and p.islower() for p in parts), f"错误码必须小写：{code}"


def test_payload_hides_internals_from_user() -> None:
    err = AppError(
        "provider.unavailable",
        message="upstream 503 from provider_x: quota drained",
    )
    payload = err.to_payload("trace-1")["error"]

    # user_message 永远来自目录，不会泄露供应商信息
    assert payload["user_message"] == "正在切换备用通道"
    assert "provider_x" not in payload["user_message"]
    # message 保留内部细节给开发排查
    assert "provider_x" in payload["message"]
    assert payload["trace_id"] == "trace-1"
    assert payload["retryable"] is True


def test_platform_fault_refunds_user() -> None:
    """平台自己在上游欠费，用户无责，必须全额退。"""
    assert ERRORS["provider.account.insufficient"].disposition is Disposition.REFUND


def test_content_rejection_counts_as_waste() -> None:
    """内容被上游拦截：产生了成本，计入废片率，但退还用户（19_UnitEconomics 2.1）。"""
    spec = ERRORS["provider.content.rejected"]
    assert spec.counts_as_waste is True
    assert spec.disposition is Disposition.REFUND


def test_quality_retry_keeps_charge() -> None:
    """质量不达标的重试确实消耗了上游算力，按约定继续计费。"""
    spec = ERRORS["quality.below_threshold"]
    assert spec.retryable is True
    assert spec.disposition is Disposition.KEEP
    assert spec.counts_as_waste is True


def test_user_side_failures_never_count_as_waste() -> None:
    """余额/预算类拦截根本没调上游，不能污染废片率统计。"""
    for code in (
        "billing.credit.insufficient",
        "billing.budget.exceeded",
        "billing.task_cap.exceeded",
        "billing.daily_cap.exceeded",
    ):
        assert ERRORS[code].counts_as_waste is False, code
        assert ERRORS[code].disposition is Disposition.RELEASE, code
