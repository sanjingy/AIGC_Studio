"""Router 层黄金集回归（22_AgentEval.md §2.1 / §6）。

跑 20 条人工标注用例，断言路线准确率 ≥ 90%、追问召回率 ≥ 85%。

**这些用例不调用任何 Provider（真实或 Mock），零成本、确定性**：
Router 20 条用 `tests.eval.eval_suite.reference_route_classifier`（透明的参照关键词
分类器）打分。为什么不跑真实 Router：`ENV=test` 写死走 MockLLM，而 MockLLM 的
router 桩只实现了 7 条路线里的 2 条，跑黄金集测出的是桩的残缺（≈35%），
不是真实提示词的判断力——把那个数字记成「Router 准确率」是骗人。详见
tests/eval/eval_suite.py 顶部 docstring。真实模型准确率只能在每周真实 Provider
链路上测（文档 §3），不在单测范围。

另有 `test_router_prompt_covers_all_routes` 对真实 router.yaml 提示词做覆盖断言，
补足参照分类器测不到的东西。
"""

from __future__ import annotations

from typing import get_args

from agents.schemas import ROUTE_TYPES
from tests.eval import eval_suite as ev

# 全部合法路线名，直接取自生产 schema 的 Literal，避免与 schemas.py 漂移
VALID_ROUTES = set(get_args(ROUTE_TYPES))


def test_golden_set_is_well_formed() -> None:
    """黄金集本身要合法：20 条、路线名合法、追问用例无路线。"""
    cases = ev.load_golden()
    assert len(cases) == 20, f"黄金集应为 20 条，实际 {len(cases)}"

    for c in cases:
        if c.expect_clarification:
            assert c.expect_route is None, f"追问用例不应标注路线：{c.input!r}"
        else:
            assert c.expect_route in VALID_ROUTES, (
                f"用例标注了非法路线 {c.expect_route!r}：{c.input!r}"
            )

    # 覆盖到全部 7 条路线 + 追问场景
    labeled = {c.expect_route for c in cases if c.expect_route}
    assert labeled == VALID_ROUTES, f"黄金集未覆盖全部路线，缺：{VALID_ROUTES - labeled}"
    assert any(c.expect_clarification for c in cases), "黄金集必须包含追问场景"


def test_route_accuracy_meets_threshold() -> None:
    score = ev.score_router()
    assert score.route_accuracy >= ev.ROUTE_ACCURACY_MIN, (
        f"路线准确率 {score.route_accuracy:.2%} < 门槛 {ev.ROUTE_ACCURACY_MIN:.0%}；"
        f"错判：{score.mispredictions}"
    )


def test_clarification_recall_meets_threshold() -> None:
    score = ev.score_router()
    assert score.clarification_recall >= ev.CLARIFICATION_RECALL_MIN, (
        f"追问召回率 {score.clarification_recall:.2%} < 门槛 "
        f"{ev.CLARIFICATION_RECALL_MIN:.0%}；漏判：{score.mispredictions}"
    )


def test_router_prompt_covers_all_routes() -> None:
    """真实 router.yaml 提示词必须列出黄金集用到的每一条路线。

    这是对生产提示词的回归守护：删掉一条路线或改错枚举名，这里会红。
    """
    missing = ev.router_prompt_covers_all_routes()
    assert not missing, f"Router 提示词缺少这些路线名：{missing}"
