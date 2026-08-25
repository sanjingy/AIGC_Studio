"""结构化输出层回归（22_AgentEval.md §2.2 / §6）。

对 agents/builtin 下**每一个**声明了 output_schema 的 Agent 验证：
  1. output_schema 指向的类名在 agents/schemas.py 里确实存在（能 resolve）
  2. 一份最小合法样例能被对应 Pydantic schema 成功解析

schema 通过率是**硬门禁**，必须 100%（文档 §2.2）。

零 Provider 调用：只做本地 Pydantic 校验，不产生任何费用。
"""

from __future__ import annotations

import pytest

from tests.eval import eval_suite as ev

# 期望被覆盖的 Agent —— 本轮新增的 5 个生产阶段 Agent 一个都不能漏
_EXPECTED_AGENTS = {
    "router.default.v1",
    "director.default.v1",
    "qa.default.v1",
    "story.default.v1",
    "visual.default.v1",
    "story.plot_index.v1",
    "story.screenplay.v1",
    "visual.character.v1",
    "visual.scene.v1",
    "visual.storyboard.v1",
}


def test_schema_pass_rate_is_100_percent() -> None:
    """硬门禁：所有 Agent 的 output_schema 都能 resolve 且样例校验通过。"""
    score = ev.score_schemas()
    failures = [r for r in score.results if not (r.resolved and r.validated)]
    assert score.pass_rate == ev.SCHEMA_PASS_RATE_REQUIRED, (
        f"schema 通过率 {score.pass_rate:.2%} ≠ 100%；失败项："
        + "; ".join(f"{r.agent_id}({r.schema_name}): {r.error}" for r in failures)
    )


def test_every_schema_agent_is_covered() -> None:
    """声明了 output_schema 的 Agent 一个都不能漏，且要涵盖本轮新增的 5 个。"""
    score = ev.score_schemas()
    covered = {r.agent_id for r in score.results}
    missing = _EXPECTED_AGENTS - covered
    assert not missing, f"这些声明了 output_schema 的 Agent 没被覆盖：{missing}"
    assert score.agents_with_schema >= len(_EXPECTED_AGENTS)


@pytest.mark.parametrize("schema_name", sorted(ev.SCHEMA_SAMPLES))
def test_sample_matches_schema(schema_name: str) -> None:
    """逐个 schema：最小样例能被对应 Pydantic 模型解析，不抛异常。"""
    model = ev.schemas.resolve(schema_name)
    model.model_validate(ev.SCHEMA_SAMPLES[schema_name])
