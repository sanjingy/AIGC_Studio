"""每个 Agent 都必须有 eval 覆盖才能上线（CLAUDE.md「不可违反的规则」）。

CLAUDE.md 写着「新 Agent 必须有 eval 用例才能上线」，理由是「提示词退化不会
让测试变红」——如果没有一条测试守着这条规则，新增一个 Agent、不写任何 eval，
CI 照样全绿，规则就只活在文档里。本测试把它变成一条真的会拦人的检查，
写法照抄 `tests/unit/test_pricing_no_constants.py`：**从权威来源枚举 + 显式判据
+ 逐个点名的失败信息**。

—— 判据怎么定（重要，诚实优先）——

「有没有 eval 覆盖」不能靠一份人工维护的 Agent id 白名单来判定——那样加新
Agent 的人只要不去改那份清单，检查就形同虚设（`test_agent_schemas.py` 里的
`_EXPECTED_AGENTS` 正是这种手动清单，本测试不复用它）。判据必须**从
`agents.registry` 实际加载到的 spec 枚举**，新增一个 Agent 会自然触发检查，
而不是要求作者记得去改清单。内置与自定义走同一条规则，第三方 Agent 不享受
特权（同 `agents/registry.py` 顶部「内置不享受特权」的原则）。

对枚举到的每一个 Agent，判据分两档，都可代码验证、都由 registry 枚举自动触发：

  1. **声明了 `output_schema`（当前 10 个 Agent 全属此档）** —— 硬门槛：
     该 schema 名必须在 `tests/eval/eval_suite.py` 的 `SCHEMA_SAMPLES` 里有一份
     样例，且样例能通过对应 Pydantic 模型校验。这直接复用上一轮已有的结构化
     输出回归脚手架，不另起一套。新增一个产出结构化数据的 Agent 时，它的新
     schema 不会凭空出现在 `SCHEMA_SAMPLES` 里 → 本测试变红，逼作者补样例。

  2. **没声明 `output_schema`（例如未来产出二进制资产的 media/TTS Agent）** ——
     schema 样例这条路不适用，改判：`tests/eval/agent_evals/` 下必须有一份
     `*.eval.yaml` 内容质量用例文件覆盖它（格式见该目录 README）。缺了同样变红。

为什么用「schema 样例」当主判据而不是要求人人都写 §2.3 的 LLM-as-judge 评分：
后者要真实 Provider、有成本、非确定性，不适合进 CI 单测；schema 样例是零成本、
确定性的结构化回归，作为「必须有 eval」的硬地板刚好合适。更重的内容质量评分
是鼓励项，不是每个 Agent 的硬性前提（`22_AgentEval.md` §2.3）。

零 Provider 调用：只做本地枚举与 Pydantic 校验，不产生任何费用。
"""

from __future__ import annotations

import pathlib
from collections.abc import Iterable, Mapping
from typing import Any

import pytest
import yaml

from agents import registry, schemas
from agents.spec import AgentSpec
from tests.eval.eval_suite import SCHEMA_SAMPLES

REPO = pathlib.Path(__file__).resolve().parents[2]

# schemaless Agent 的回退判据所在目录（随 tests/ 被 compose 挂载，容器内可见）。
AGENT_EVAL_DIR = REPO / "tests" / "eval" / "agent_evals"


def _content_eval_fixture_covers(agent_id: str, dirs: Iterable[pathlib.Path]) -> bool:
    """dirs 里是否存在一份覆盖 agent_id 的内容质量用例（*.eval.yaml）。

    只校验「文件存在、能解析、agent 对得上、cases 非空」——证明这个 Agent 的
    提示词进了回归用例集。不在这里跑 LLM-as-judge 打分（§2.3，非单测范围）。
    """
    for directory in dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.eval.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError:
                continue
            if not isinstance(data, dict):
                continue
            cases = data.get("cases")
            if data.get("agent") == agent_id and isinstance(cases, list) and cases:
                return True
    return False


def _coverage_gap(
    spec: AgentSpec,
    *,
    eval_dirs: Iterable[pathlib.Path],
    samples: Mapping[str, Any],
) -> str | None:
    """返回这个 Agent 缺 eval 覆盖的原因；已覆盖则返回 None。"""
    schema_name = spec.output_schema
    if schema_name:
        # 第 1 档：声明了 output_schema —— schema 样例是硬门槛
        try:
            model = schemas.resolve(schema_name)
        except LookupError:
            return f"output_schema {schema_name!r} 在 agents/schemas.py 里不存在，无法回归"
        sample = samples.get(schema_name)
        if sample is None:
            return (
                f"output_schema {schema_name!r} 没有 SCHEMA_SAMPLES 回归样例"
                f"（去 tests/eval/eval_suite.py 补一份最小合法样例）"
            )
        try:
            model.model_validate(sample)
        except Exception as exc:  # 收集失败原因，不中断枚举
            return f"output_schema {schema_name!r} 的 SCHEMA_SAMPLES 样例无法通过校验：{exc}"
        return None

    # 第 2 档：没声明 output_schema —— 退回内容质量用例文件
    if _content_eval_fixture_covers(spec.id, eval_dirs):
        return None
    return (
        "没声明 output_schema，也没有 tests/eval/agent_evals/*.eval.yaml 内容质量用例"
        f"（文件里 agent 应写 {spec.id!r}，格式见该目录 README）"
    )


def _uncovered_agents(
    specs: Iterable[AgentSpec],
    *,
    eval_dirs: Iterable[pathlib.Path] = (AGENT_EVAL_DIR,),
    samples: Mapping[str, Any] = SCHEMA_SAMPLES,
) -> dict[str, str]:
    """枚举缺 eval 覆盖的 Agent → {agent_id: 原因}。"""
    gaps: dict[str, str] = {}
    for spec in specs:
        why = _coverage_gap(spec, eval_dirs=eval_dirs, samples=samples)
        if why is not None:
            gaps[spec.id] = why
    return gaps


def _format_gap_report(gaps: Mapping[str, str]) -> str:
    lines = "\n".join(f"  - {aid}: {why}" for aid, why in sorted(gaps.items()))
    return f"以下 Agent 没有 eval 覆盖，不得上线（CLAUDE.md：新 Agent 必须有 eval 用例）：\n{lines}"


# ------------------------------------------------------------------ 真实门禁


def test_every_loaded_agent_has_eval_coverage() -> None:
    """当前实际加载的每一个 Agent（builtin + custom）都必须有 eval 覆盖。

    这条覆盖现存的 5 个新增阶段 Agent（story.plot_index.v1 等）与旧 Agent，
    因为二者都来自同一份 registry 枚举，没有豁免。
    """
    report = registry.registry(reload=True)
    assert not report.errors, f"有 Agent spec 加载失败，先修好再谈 eval 覆盖：{report.errors}"
    assert report.specs, "registry 没加载到任何 Agent —— 检查会空过，等于没跑"

    gaps = _uncovered_agents(report.specs.values())
    assert not gaps, _format_gap_report(gaps)


def test_new_and_legacy_agents_all_pass_today() -> None:
    """现状确认（任务书要求 #4）：5 个新增阶段 Agent 与旧 Agent 都判为已覆盖。

    下面这两份 id 集合**不是判据**（判据在 test_every_loaded_agent_has_eval_coverage
    里，纯从 registry 枚举、无白名单）；它们只是「现在都跑得通」的现状快照 +
    「registry 确实把这些 Agent 都加载了」的防回归哨兵。加新 Agent 的人不需要来改
    这里——真正拦人的是上面那条枚举式门禁。
    """
    report = registry.registry(reload=True)
    loaded = set(report.specs)
    gaps = _uncovered_agents(report.specs.values())
    covered = loaded - set(gaps)

    new_stage_agents = {
        "story.plot_index.v1",
        "story.screenplay.v1",
        "visual.character.v1",
        "visual.scene.v1",
        "visual.storyboard.v1",
    }
    legacy_agents = {
        "router.default.v1",
        "director.default.v1",
        "qa.default.v1",
        "story.default.v1",
        "visual.default.v1",
    }
    assert new_stage_agents <= covered, (
        f"新增阶段 Agent 未全部覆盖：{new_stage_agents - covered}（gaps={gaps}）"
    )
    assert legacy_agents <= covered, (
        f"旧 Agent 未全部覆盖：{legacy_agents - covered}（gaps={gaps}）"
    )
    # 全集都被覆盖，没有任何已加载的 Agent 逃过门禁
    assert covered == loaded, f"仍有 Agent 缺覆盖：{loaded - covered}"


# ---------------------------------------------- 自证反例：这条检查真的会拦人


def test_gate_rejects_a_fake_agent_without_eval() -> None:
    """故意注册一个没有 eval 覆盖的假 Agent，证明门禁会红（不是摆设）。

    参照 test_pricing_no_constants.py 的思路：造一个违规样本，断言检查抓得到它，
    且只点名它、不牵连真 Agent。假 Agent 只在内存里构造，不落地成 yaml，
    因此不污染 registry。
    """
    real = registry.registry(reload=True).specs
    fake = AgentSpec(
        id="story.fake_uncovered.v1",
        name="缺 eval 的假 Agent",
        role="story",
        output_schema="TotallyMadeUpSchema",  # 不在 agents/schemas.py 里
    )

    gaps = _uncovered_agents([*real.values(), fake])

    # 假 Agent 被点名，真 Agent 一个都不牵连
    assert set(gaps) == {"story.fake_uncovered.v1"}, f"点名不精确：{gaps}"

    # 真实门禁那句 assert 换上这份 specs 一定会 AssertionError（即 CI 变红）
    with pytest.raises(AssertionError) as excinfo:
        assert not gaps, _format_gap_report(gaps)
    assert "story.fake_uncovered.v1" in str(excinfo.value)


def test_gate_flags_registered_schema_without_sample() -> None:
    """精确命中「schema 能 resolve 但 SCHEMA_SAMPLES 缺样例」这一档。

    用真实 Agent + 一个空的 samples 集模拟「有人加了新 schema 却忘了补样例」。
    """
    spec = registry.get("router.default.v1")
    assert spec is not None
    gaps = _uncovered_agents([spec], samples={})
    assert "router.default.v1" in gaps
    assert "SCHEMA_SAMPLES" in gaps["router.default.v1"]


def test_schemaless_agent_without_fixture_is_flagged(tmp_path: pathlib.Path) -> None:
    """没 output_schema 又没内容质量用例文件的 Agent，判为缺覆盖。"""
    bare = AgentSpec(id="media.tts.v1", name="无 schema 的 media Agent", role="media")
    gaps = _uncovered_agents([bare], eval_dirs=(tmp_path,))
    assert "media.tts.v1" in gaps
    assert "agent_evals" in gaps["media.tts.v1"]


def test_schemaless_agent_covered_by_content_fixture(tmp_path: pathlib.Path) -> None:
    """回退判据的正路径：放一份 *.eval.yaml 后，schemaless Agent 就算已覆盖。"""
    fixture = (
        "agent: media.tts.v1\n"
        "cases:\n"
        "  - name: 基本朗读\n"
        "    input: 你好\n"
        "    expect_nonempty: true\n"
    )
    (tmp_path / "media_tts.eval.yaml").write_text(fixture, encoding="utf-8")
    bare = AgentSpec(id="media.tts.v1", name="无 schema 的 media Agent", role="media")
    gaps = _uncovered_agents([bare], eval_dirs=(tmp_path,))
    assert "media.tts.v1" not in gaps

    # 空 cases 的文件不算覆盖——防止放个空壳文件糊弄检查
    (tmp_path / "media_tts.eval.yaml").write_text(
        "agent: media.tts.v1\ncases: []\n", encoding="utf-8"
    )
    gaps = _uncovered_agents([bare], eval_dirs=(tmp_path,))
    assert "media.tts.v1" in gaps
