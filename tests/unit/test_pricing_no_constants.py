"""价格不得写死（ADR-014）。

这是一条用测试守住的架构约束，不是风格偏好。

DeepSeek 于 2026-08-17 起高峰输出价上涨 350%。把单价写进代码的实现
会在那一夜毛利转负，而且必须改代码、过 CI、重新发版才能止损。
所有价格走 model_pricing 表，所有系数走 pricing_rules 表，改数不改码。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from apps.api.modules.billing.pricing import apply_retry_factor

REPO = pathlib.Path(__file__).resolve().parents[2]

# 只扫业务代码。测试和迁移里出现数字是正常的。
SCANNED = [
    REPO / "apps" / "api" / "modules",
    REPO / "worker",
]

# 这些标识符出现在赋值左侧时，右侧不允许是字面量数字
PRICE_LIKE = (
    "price",
    "cost_per",
    "unit_price",
    "provider_cost",
    "credit_price",
    "exchange_rate",
    "retry_factor",
)

# 已知的合法例外，必须显式列举并写明理由
ALLOWED = {
    # 熔断兜底值：库里没配时的最后防线，不是定价
    "apps/api/modules/billing/service.py",
    # Mock 任务的计费基数，S6 接真实 Provider 后删除
    "apps/api/modules/billing/pricing.py",
}


def _iter_py() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for root in SCANNED:
        files.extend(p for p in root.rglob("*.py") if "__pycache__" not in str(p))
    return files


@pytest.mark.parametrize("path", _iter_py(), ids=lambda p: str(p.name))
def test_no_hardcoded_prices(path: pathlib.Path) -> None:
    rel = path.relative_to(REPO).as_posix()
    if rel in ALLOWED:
        pytest.skip(f"{rel} 在显式豁免清单里")

    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign | ast.AnnAssign):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [t.id.lower() for t in targets if isinstance(t, ast.Name)]
        if not any(k in n for n in names for k in PRICE_LIKE):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, int | float):
            offenders.append(f"L{node.lineno}: {names} = {node.value.value}")

    assert not offenders, (
        f"{rel} 里写死了价格：{offenders}\n价格必须走 model_pricing / pricing_rules 表（ADR-014）"
    )


def test_no_float_arithmetic_on_money() -> None:
    """金额计算不能出现浮点。

    0.1 + 0.2 != 0.3，用浮点存钱是财务事故的经典来源。
    整个系统的金额都是 BIGINT 最小单位。
    """
    offenders: list[str] = []
    for path in _iter_py():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.annotation, ast.Name)
                and node.annotation.id == "float"
                and isinstance(node.target, ast.Name)
                and any(
                    k in node.target.id.lower()
                    for k in ("cost", "price", "amount", "balance", "credit")
                )
            ):
                offenders.append(f"{path.name}:L{node.lineno} {node.target.id}: float")
    assert not offenders, f"金额字段用了浮点：{offenders}"


def test_retry_factor_uses_integer_math() -> None:
    """废片率存成整数百分比，避免浮点进入钱的计算。"""
    assert apply_retry_factor(100, 250) == 250  # 2.5 次
    assert apply_retry_factor(100, 150) == 150  # 1.5 次
    assert apply_retry_factor(100, 100) == 100  # 无重试
    assert isinstance(apply_retry_factor(33, 250), int)
