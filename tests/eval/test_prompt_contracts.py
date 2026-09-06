"""提示词契约回归（ADR-037 落地的那几处提示词改动）。

CLAUDE.md 的硬规则：**改 Agent 提示词必须有 eval 用例**，理由是"提示词退化
不会让测试变红"。ADR-037 往四份提示词里加了三类东西，而这三类**一条都不在
`output_schema` 上**，schema 样例那道门禁一个都拦不住：

1. **受控词表**（身高 / 体型 / 体态）——schema 会拒掉词表外的值，但提示词里
   不列出词表，模型根本不知道能选什么，只会一直吐 schema 校验失败的自由文本；
2. **"不得默认套用本国"** ——这是 ADR-037 第 2 条的明文禁令，删掉它模型会
   退回训练分布的默认，产出仍然完全合法的 `ethnicity: 中国现代人`；
3. **变量占位符** ——`{adaptation_instruction}` 被删掉，用户在门① 选的洗稿
   永远进不了提示词，他会拿到一份改编稿，而且无处可查。

判据不手抄：受控词表从 `agents/schemas.py` 的 Literal 取，加一个词而不更新
提示词就会红。这是这份用例相对"人工检查提示词"的唯一价值。

零 Provider 调用：只读 registry 里的 spec 文本。
"""

from __future__ import annotations

import pytest

from agents import registry, schemas
from tests.eval import eval_suite as ev

# ADR-037 改过提示词的四个 Agent。少写一个就等于那一个没有 eval 覆盖。
CHANGED_AGENTS = (
    "story.plot_index.v1",
    "story.screenplay.v1",
    "visual.character.v1",
    "visual.scene.v1",
)


def test_changed_agents_are_all_under_contract() -> None:
    """这一轮改过的四份提示词，每一份都要在契约表里有条目。"""
    assert set(CHANGED_AGENTS) <= set(ev.prompt_contracts())


@pytest.mark.parametrize("agent_id", CHANGED_AGENTS)
def test_prompt_keeps_its_contract(agent_id: str) -> None:
    """逐个 Agent：契约里要求出现的串，一个都不能少。"""
    gaps = ev.prompt_contract_gaps({agent_id: ev.prompt_contracts()[agent_id]})
    assert not gaps, f"{agent_id} 的提示词掉了这些内容：{gaps[agent_id]}"


def test_no_agent_prompt_violates_its_contract() -> None:
    """全量：契约表里的每一条都满足。"""
    gaps = ev.prompt_contract_gaps()
    assert not gaps, f"提示词契约有缺口：{gaps}"


# ---------------------------------------------------------------- 受控词表


@pytest.mark.parametrize(
    "literal_name",
    ["HEIGHT_BANDS", "BODY_TYPES", "POSTURES"],
)
def test_every_vocabulary_word_appears_in_the_character_prompt(literal_name: str) -> None:
    """词表里的每一个词都要在角色档案提示词里列出来。

    从 schema 的 Literal 枚举，不手抄一份清单：手抄的清单在有人往词表里
    加词时不会自己变长，而那正是这条用例要拦的退化——schema 认识新词、
    提示词没告诉模型有这个词，等于加了个永远选不到的选项。
    """
    spec = registry.get("visual.character.v1")
    assert spec is not None
    words = ev._vocabulary(getattr(schemas, literal_name))
    missing = [w for w in words if w not in spec.prompt]
    assert not missing, f"{literal_name} 的这些词没写进提示词：{missing}"


def test_the_vague_words_stay_out_of_the_vocabulary() -> None:
    """ "匀称""普通""正常身材"不许进词表，也不许出现在提示词的可选项里。

    这三个词在扩散模型里等价于没写。它们是"同一个角色两次生成不是同一个人"
    的直接来源，也是 `reference_embedding` 至今为空的根因之一——一段
    等价于空的描述做不出可比的向量。

    提示词里可以**点名禁止**它们（现在就是这么写的），所以这里只断言它们
    不在词表取值里，不断言它们不出现在提示词全文中。
    """
    vague = {"匀称", "普通", "正常身材", "体型适中"}
    for literal_name in ("HEIGHT_BANDS", "BODY_TYPES", "POSTURES"):
        words = set(ev._vocabulary(getattr(schemas, literal_name)))
        assert not (words & vague), f"{literal_name} 里混进了模糊词：{words & vague}"


def test_vocabulary_words_are_distinct_enough_to_draw() -> None:
    """三张词表之间不能有重复的词。

    同一个词出现在两张词表里，模型选哪张都"对"，而拼出来的 build 会变成
    "中等身高，中等身高"这种。词表的全部价值就是每个词指向一件不同的事。
    """
    height = set(ev._vocabulary(schemas.HEIGHT_BANDS))
    body = set(ev._vocabulary(schemas.BODY_TYPES))
    posture = set(ev._vocabulary(schemas.POSTURES))
    assert not (height & body)
    assert not (height & posture)
    assert not (body & posture)


# ---------------------------------------------------------------- 改编模式


def test_both_adaptation_modes_render_a_real_instruction() -> None:
    """两种改编模式都要渲染出一段**完整的要求**，不是一个模式名。

    提示词里出现的是完整指令，Agent 不需要知道有几种模式，也不需要
    自己解释 "rewrite" 是什么意思——它对 rewrite 的理解和我们的可能不同，
    而那个分歧要到成片对照原著时才发现。
    """
    from apps.api.modules.agent import orchestrator

    adapt = orchestrator.adaptation_instruction("adapt")
    rewrite = orchestrator.adaptation_instruction("rewrite")

    assert "改编" in adapt and "不要替换任何人名" in adapt
    assert "洗稿" in rewrite and "换成原创" in rewrite
    assert adapt != rewrite

    # 未知模式退回改编（保守）：洗稿会改人名，猜错的代价比反过来大得多
    assert orchestrator.adaptation_instruction("nonsense") == adapt


def test_era_fallback_asks_for_a_judgement_instead_of_naming_a_default() -> None:
    """判不出时代背景时给的是**要求判定的指令**，不是一个具体人种。

    ADR-037 第 2 条：判定结果不得默认套用本国。给一个具体人种等于
    替模型做了这个判断，而它会一路传导到角色、服装、建筑、街景。
    """
    from apps.api.modules.agent import orchestrator

    era, ethnicity = orchestrator._era_of({}, None)
    assert era == "未判定"
    assert "不得默认套用" in ethnicity
    assert "中国现代人" not in ethnicity.replace("不得默认套用中国现代人", "")
