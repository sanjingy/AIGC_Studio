"""四类成品提示词的 MockLLM builder：产出必须同时过 schema 和 `rules` 校验。

—— 为什么这件事值一个独立的测试文件 ——

`prompting.service._verify` 会拿 `rules.check_style` / `rules.check_output` 逐条
查模型的产出。MockLLM 的四个 builder 因此**不能只保证形状合法**：形状对、内容
不合格时，整条新链路的集成测试会全红，而且红在校验器里——报错写的是"三重否定
缺了"，看起来像生产代码的 bug，实际是 Mock 没写对。那种红查起来极贵。

所以这里把两件事一次钉死：

1. **正路径**：四个 builder 的产出过 pydantic schema，且 `check_style` 与
   `check_output` 都返回空问题列表。走的是 `MockLLM.complete` 而不是直接调私有
   函数——`_BUILDERS` 里漏注册一个 schema 名，MockLLM 会抛
   `agent.output.schema_invalid`，而那正是最容易发生、也最难从集成测试的报错里
   看出来的一种失败。
2. **反路径**：每一条校验规则真的拦得住退化。只测正路径的话，有人把
   `check_output` 改成 `return []`，这个文件仍然全绿。

`tests/eval/eval_suite.py` 的四份 `SCHEMA_SAMPLES` 也在这里过一遍同样的校验：
样例是提示词作者最先照抄的东西，样例本身不合格就会教出不合格的提示词。

零 Provider 调用：`MockLLM` 是纯函数 + 哈希种子，不碰网络也不碰数据库。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agents import schemas
from apps.api.core.errors import AppError
from apps.api.modules.agent import llm
from apps.api.modules.prompting import rules
from tests.eval.eval_suite import SCHEMA_SAMPLES

# --------------------------------------------------------------------- 上下文
#
# 下面几份 fixture 是 `prompting.context.build()` 产出的 `Basis.context` 的形状。
# 手写而不是去跑 `build()`：那要数据库。字段名对不上时反而是好事——说明
# `context.py` 改了结构而这里没跟上，正是该红的时候。

STYLE_TOKENS = "日式动画风格，赛璐璐上色，清晰线稿"
RENDER_MODE = "赛璐璐"
LINE_WEIGHT = "中等线宽"
TONE = "柔和自然的冷调，低饱和"

ERA = {
    "era": "现代",
    "region": "日本",
    "ethnicity": "东亚面孔",
    "evidence": "警视厅、资料馆等称谓",
    "confirmed": "已确认",
}

ANCHORS = [
    {"name": "锈迹铁门", "description": "画面正前方的双开滑动铁门，右扇下缘锈穿一个洞"},
    {"name": "门柱铜牌", "description": "铁门右侧砖柱上齐胸高的白底黑字铜牌"},
]

LIGHTING = {"name": "上午", "description": "均匀自然日光自左上方射入，阴影清晰短小"}

#: 同一个场景声明过、但**这一次没被引用**的另一个状态。两种光同时写进提示词时
#: 模型只会自己挑一个，所以它必须拦得住——没有这一份，那条规则测不了。
OTHER_LIGHTING = {"name": "傍晚", "description": "低角度侧光从围墙西侧射入，阴影拉长"}


def _style(surface: str, *, with_tone: bool = True, **over: Any) -> dict[str, Any]:
    block: dict[str, Any] = {
        "surface": surface,
        "tokens": STYLE_TOKENS,
        "render_mode": RENDER_MODE,
        "line_weight": LINE_WEIGHT,
    }
    if with_tone:
        block["color_grading"] = TONE
    block.update(over)
    return block


def _character(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ref": "zhu_jue",
        "name": "主角",
        "kind": "人类",
        "identity": "被调职的刑警",
        "nationality": "日本",
        "ethnicity": "东亚面孔",
        # 结构化外貌事实。逐项齐全而不是只留发型服装：校验器现在按
        # `rules.known_appearance` 逐条查成品词，fixture 少一项就少测一条。
        # `outfit` 刻意写成三段——"深灰西装外套，白衬衫，无领带"被归纳成
        # "西装"是实测最常见的退化，而它只有在多段值上才表现得出来。
        "age_range": "30 出头",
        "hair": "黑色短发，额前碎发",
        "eyes": "深褐色，眼神沉稳",
        "face": "轮廓分明",
        "build": "中等身高，精瘦结实",
        "outfit": "深灰西装外套，白衬衫，无领带",
        "skin": "自然健康肤色",
        "shoes": "黑色皮鞋",
        "distinctive": "左手常握成拳",
        "accessories": "警号铜牌",
        "appearance": {},
    }
    base.update(over)
    return base


def _scene(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ref": "gate",
        "name": "资料馆门口",
        "setting": "爬满爬山虎的院墙与锈迹铁门",
        "key_elements": ["铁门", "门柱牌子"],
        "camera_axis": {
            "position": "铁门外的路面",
            "facing": "朝向建筑正面",
            "far_end": "红砖建筑正门石阶",
        },
        "fixed_references": ANCHORS,
        # 这个场景声明了两个光照状态，本次只引用「上午」。
        "lighting_states": [LIGHTING, OTHER_LIGHTING],
        "default_lighting": "上午",
    }
    base.update(over)
    return base


def _shot(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "index": 1,
        "node_index": 1,
        "scene_ref": "gate",
        "character_refs": ["zhu_jue"],
        "shot_size": "全景",
        "angle": "斜前方45度",
        "camera_move": "缓缓推近镜头",
        "content": "主角站在铁门外抬头看门柱铜牌",
        "lighting_ref": "上午",
        "speaker_ref": "",
        "dialogue": "",
        "sfx": "",
    }
    base.update(over)
    return base


def character_ctx(**over: Any) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "kind": rules.KIND_CHARACTER,
        "rule_version": rules.RULE_VERSION,
        "subject_key": "zhu_jue",
        # 角色立绘**不带**全局色调：基准图带上戏剧化色调会污染相似度比对
        "style": _style("人物版", with_tone=False),
        "era": ERA,
        "character": _character(),
    }
    ctx.update(over)
    return ctx


def scene_ctx(**over: Any) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "kind": rules.KIND_SCENE,
        "rule_version": rules.RULE_VERSION,
        "subject_key": "gate",
        "style": _style("场景版"),
        "era": ERA,
        "scene": _scene(),
        "lighting": LIGHTING,
    }
    ctx.update(over)
    return ctx


def shot_ctx(kind: str = rules.KIND_SHOT_IMAGE, **over: Any) -> dict[str, Any]:
    surface = "视频版" if kind == rules.KIND_SHOT_VIDEO else "人物版"
    ctx: dict[str, Any] = {
        "kind": kind,
        "rule_version": rules.RULE_VERSION,
        "subject_key": "1",
        "style": _style(surface),
        "era": ERA,
        "shot": _shot(),
        "characters": [_character()],
        "scene": _scene(),
        "lighting": LIGHTING,
        "anchor_card": {
            "scene_name": "资料馆门口",
            "camera_axis": _scene()["camera_axis"],
            "fixed_references": ANCHORS,
            "lighting_tone": LIGHTING["description"],
        },
    }
    ctx.update(over)
    return ctx


# ------------------------------------------------------------------- 跑 Mock


SCHEMA_OF_KIND = {
    rules.KIND_CHARACTER: "CharacterPortraitPrompt",
    rules.KIND_SCENE: "SceneViewsPrompt",
    rules.KIND_SHOT_IMAGE: "ShotFramePrompt",
    rules.KIND_SHOT_VIDEO: "ShotVideoPrompt",
}


async def build(kind: str, ctx: dict[str, Any], instruction: str = "") -> dict[str, Any]:
    """跑一次 MockLLM，返回它产出的 dict。

    `user` 逐字照 `prompting.service._user_input()` 拼：上下文 JSON + 这次的
    额外要求。builder 要从这段 JSON 里解出风格词与档案，拼错了它就解不出来。
    """
    payload = json.dumps({**ctx, "instruction": instruction}, ensure_ascii=False, sort_keys=True)
    response = await llm.MockLLM().complete(
        llm.LLMRequest(
            system="（提示词正文不影响 Mock）",
            user=payload,
            schema_name=SCHEMA_OF_KIND[kind],
            max_output_tokens=4000,
        )
    )
    return dict(json.loads(response.text))


def verify(kind: str, output: dict[str, Any], ctx: dict[str, Any]) -> list[str]:
    """`prompting.service._verify` 的两道校验合成一份问题列表。"""
    prompt = str(output.get("prompt", ""))
    return [
        *rules.check_style(prompt, style=ctx["style"]),
        *rules.check_output(kind, output, ctx),
    ]


# --------------------------------------------------------- 正路径：四类全过


@pytest.mark.parametrize(
    ("kind", "ctx_factory"),
    [
        (rules.KIND_CHARACTER, character_ctx),
        (rules.KIND_SCENE, scene_ctx),
        (rules.KIND_SHOT_IMAGE, lambda: shot_ctx(rules.KIND_SHOT_IMAGE)),
        (rules.KIND_SHOT_VIDEO, lambda: shot_ctx(rules.KIND_SHOT_VIDEO)),
    ],
)
async def test_builder_output_passes_schema_and_rules(kind: str, ctx_factory: Any) -> None:
    """四个 builder 的产出同时过 schema 与 `rules`。

    这是整条新链路能在 `ENV=test` 下跑通的前提：任何一个不过，出图与准备
    提示词的集成用例会全红，且红在校验器里。
    """
    ctx = ctx_factory()
    output = await build(kind, ctx)

    # 1. schema：`_BUILDERS` 注册了、字段齐、长度与取值合法
    schemas.resolve(SCHEMA_OF_KIND[kind]).model_validate(output)

    # 2. 规则：风格词原样保留 + 模板硬要求
    assert verify(kind, output, ctx) == []


async def test_unregistered_schema_is_an_explicit_error() -> None:
    """没注册 builder 的 schema 要明确报错，不能返回一段能过校验的空话。

    这条守的是"四个 builder 真的注册进了 `_BUILDERS`"这件事的反面：
    漏注册时 MockLLM 必须炸，而不是让链路以某种降级形态继续跑。
    """
    with pytest.raises(AppError) as excinfo:
        await llm.MockLLM().complete(
            llm.LLMRequest(
                system="", user="{}", schema_name="TotallyMadeUpSchema", max_output_tokens=100
            )
        )
    assert excinfo.value.code == "agent.output.schema_invalid"


async def test_same_context_gives_the_same_prompt() -> None:
    """同样的上下文必须得到同样的词。

    不确定的 Mock 会让测试时红时绿，比没有 Mock 更糟；而提示词复用判据是
    上下文摘要，Mock 每次产出不同会让"复用"这条路径根本测不了。
    """
    ctx = character_ctx()
    assert await build(rules.KIND_CHARACTER, ctx) == await build(rules.KIND_CHARACTER, ctx)


# ----------------------------------------------- 锁定风格词：缺一个词就不合格


@pytest.mark.parametrize(
    ("kind", "ctx_factory"),
    [
        (rules.KIND_CHARACTER, character_ctx),
        (rules.KIND_SCENE, scene_ctx),
        (rules.KIND_SHOT_IMAGE, lambda: shot_ctx(rules.KIND_SHOT_IMAGE)),
        (rules.KIND_SHOT_VIDEO, lambda: shot_ctx(rules.KIND_SHOT_VIDEO)),
    ],
)
async def test_rewritten_style_token_is_rejected(kind: str, ctx_factory: Any) -> None:
    """风格词被改写一个字就判不合格（ADR-036 第 4 条）。

    模拟真模型最常见的退化：它把"赛璐璐上色"精简成"赛璐璐"。产出照样过
    schema，但那一镜已经不在这部片子的画风里了。
    """
    ctx = ctx_factory()
    output = await build(kind, ctx)
    output["prompt"] = str(output["prompt"]).replace("赛璐璐上色", "赛璐璐")

    problems = rules.check_style(str(output["prompt"]), style=ctx["style"])
    assert problems, "风格词被精简了却判成合格"
    assert "赛璐璐上色" in problems[0]


async def test_missing_global_tone_is_rejected() -> None:
    """全局色调没写进去也算不合格。

    色调是 B4 定给整部片子的那一个，所有场景与镜头共用。漏掉它，每个场景
    会各自被渲成一个颜色，剪在一起不像同一部片。
    """
    ctx = scene_ctx()
    output = await build(rules.KIND_SCENE, ctx)
    output["prompt"] = str(output["prompt"]).replace(TONE, "")

    problems = rules.check_style(str(output["prompt"]), style=ctx["style"])
    assert any("全局色调" in p for p in problems), problems


# ------------------------------------------------------- B3：身份与关键配件


async def test_character_identity_comes_from_the_profile() -> None:
    """档案上的国籍必须原样出现在提示词里。"""
    ctx = character_ctx()
    output = await build(rules.KIND_CHARACTER, ctx)

    assert output["subject_identity"] == "日本"
    assert "日本" in output["prompt"]
    assert verify(rules.KIND_CHARACTER, output, ctx) == []


async def test_identity_rewritten_to_a_broader_word_is_caught() -> None:
    """把档案上的「日本」改写成「东亚」要被抓住。

    这是 Lead 审查 A4 那条：只查模型**自报**的 identity 是不够的——它可以
    自报「东亚」、提示词里也确实有「东亚」，三项自洽、校验全绿，而这个角色
    的国籍已经被悄悄换掉了，五官服装发型会跟着一路错下去。
    """
    ctx = character_ctx()
    output = await build(rules.KIND_CHARACTER, ctx)
    output["prompt"] = str(output["prompt"]).replace("日本", "东亚")
    output["subject_identity"] = "东亚"

    problems = rules.check_output(rules.KIND_CHARACTER, output, ctx)
    assert any("日本" in p for p in problems), problems


async def test_profile_accessories_must_survive_into_the_prompt() -> None:
    """档案写了关键配件，提示词里就不许省掉。

    实测最常见的退化是模型图省事一律填「无」，而抹掉的往往正是这个角色的
    识别物（钥匙串、腰牌、眼镜）。
    """
    ctx = character_ctx()
    output = await build(rules.KIND_CHARACTER, ctx)
    assert "警号铜牌" in output["prompt"]
    assert output["accessories"] == "警号铜牌"

    # 退化：配件被抹成「无」
    output["accessories"] = "无"
    output["prompt"] = str(output["prompt"]).replace("关键配件：警号铜牌。", "")
    problems = rules.check_output(rules.KIND_CHARACTER, output, ctx)
    assert any("警号铜牌" in p for p in problems), problems


async def test_profile_without_accessories_may_say_none() -> None:
    """档案确实没写配件时填「无」是对的，不该被判不合格。

    "档案没写"和"档案写了没有"是两件事。混成一个判据会让校验器要么放过
    漏配件，要么要求每张立绘都出现「无」这两个字。
    """
    ctx = character_ctx(character=_character(accessories=""))
    output = await build(rules.KIND_CHARACTER, ctx)

    assert output["accessories"] == "无"
    assert verify(rules.KIND_CHARACTER, output, ctx) == []


async def test_human_portrait_keeps_the_four_iron_rules() -> None:
    """人类立绘必须写「无任何表情」，且不许出现背包书包。"""
    ctx = character_ctx()
    output = await build(rules.KIND_CHARACTER, ctx)
    assert output["template"] == "人类"
    assert "无任何表情" in output["prompt"]

    # 带情绪的基准立绘当不了一致性基准
    stripped = dict(output, prompt=str(output["prompt"]).replace("无任何表情", "微笑"))
    assert any("无任何表情" in p for p in rules.check_output(rules.KIND_CHARACTER, stripped, ctx))

    # 四铁律点名禁止的道具
    with_bag = dict(output, prompt=f"{output['prompt']}背着一个黑色背包。")
    assert any("背包" in p for p in rules.check_output(rules.KIND_CHARACTER, with_bag, ctx))


async def test_non_human_character_uses_the_other_template() -> None:
    """非人类角色走模板 B，且不套用人类的五官妆容规则。

    拿人类的五官/妆容/鞋子去套一头狼，出来的不是差一点，是错的。
    """
    ctx = character_ctx(
        character=_character(
            kind="怪物", nationality="", ethnicity="", species="石像鬼", accessories=""
        ),
        era={**ERA, "ethnicity": ""},
    )
    output = await build(rules.KIND_CHARACTER, ctx)

    assert output["template"] == "非人类"
    assert output["subject_identity"] == "石像鬼"
    # 人类那两条只对人类模板生效，非人类不该因为没写「无任何表情」被判不合格
    assert verify(rules.KIND_CHARACTER, output, ctx) == []


# ----------------------------------------------------------- B5：四视图无人


async def test_scene_views_are_a_2x2_grid_with_all_four_labels() -> None:
    """四格格名一个都不能少，且自检字段要与正文对得上。"""
    ctx = scene_ctx()
    output = await build(rules.KIND_SCENE, ctx)

    assert "2x2" in output["prompt"]
    for _field, label in rules.QUADRANT_LABELS:
        assert label in output["prompt"], f"缺了{label}"
    assert set(output["quadrants"]) == {"top_left", "top_right", "bottom_left", "bottom_right"}
    assert verify(rules.KIND_SCENE, output, ctx) == []


async def test_a_dropped_quadrant_is_rejected() -> None:
    """漏掉一格就不是四视图了。"""
    ctx = scene_ctx()
    output = await build(rules.KIND_SCENE, ctx)
    output["prompt"] = str(output["prompt"]).replace("右下格", "")

    problems = rules.check_output(rules.KIND_SCENE, output, ctx)
    assert any("右下格" in p for p in problems), problems


@pytest.mark.parametrize("clause", rules.NO_PEOPLE_CLAUSES)
async def test_every_no_people_clause_is_required(clause: str) -> None:
    """三重否定缺任意一句都不合格。

    三句分别封堵"画出人"、"描述动作"、"隐含人存在"三条路径。只写一句实测
    仍会漏进人影，而场景基准图里有人就当不了基准——那个人会被当成这个空间
    的一部分带进后续每一镜。
    """
    ctx = scene_ctx()
    output = await build(rules.KIND_SCENE, ctx)
    assert clause in output["prompt"]

    output["prompt"] = str(output["prompt"]).replace(clause, "")
    problems = rules.check_output(rules.KIND_SCENE, output, ctx)
    assert any(clause in p for p in problems), problems


async def test_scene_locks_its_elements_by_anchor_name() -> None:
    """元素锁定清单收的是锚点**名称**，不是锚点字典的 repr。

    `fixed_references` 在库里是两段式字典。直接 `str()` 会把
    `{'name': ..., 'description': ...}` 整段拼进提示词，校验器照样放行
    （那一项确实"出现在提示词里"），但产出的是一段没人看得懂的字符串，
    而 Mock 的产出同时是下游用例的输入样例。
    """
    ctx = scene_ctx()
    output = await build(rules.KIND_SCENE, ctx)

    assert "锈迹铁门" in output["fixed_elements"]
    assert "门柱铜牌" in output["fixed_elements"]
    assert not any("description" in e for e in output["fixed_elements"]), output["fixed_elements"]
    assert "{'name'" not in output["prompt"]


async def test_scene_prompt_says_no_text_labels() -> None:
    """AI 生成的文字几乎必然是乱码，会毁掉整张参考页。"""
    ctx = scene_ctx()
    output = await build(rules.KIND_SCENE, ctx)
    output["prompt"] = str(output["prompt"]).replace("无文字标注", "")

    assert any("无文字标注" in p for p in rules.check_output(rules.KIND_SCENE, output, ctx))


# ------------------------------------------------- 首帧：景别/角度与五要素


async def test_shot_frame_copies_shot_size_and_angle_from_the_storyboard() -> None:
    """景别与角度照抄分镜表——那是用户在工作台上定过的。"""
    ctx = shot_ctx(rules.KIND_SHOT_IMAGE)
    output = await build(rules.KIND_SHOT_IMAGE, ctx)

    assert output["shot_size"] == "全景"
    assert output["angle"] == "斜前方45度"
    assert "全景" in output["prompt"]
    assert "斜前方45度" in output["prompt"]
    assert verify(rules.KIND_SHOT_IMAGE, output, ctx) == []


async def test_changing_shot_size_changes_the_prompt() -> None:
    """改了景别，产出的词必须跟着变。

    在这之前出图只传 `content`，用户改的景别全部丢在路上：界面显示改成功
    了，出来的图和改之前一样。
    """
    wide = await build(rules.KIND_SHOT_IMAGE, shot_ctx(rules.KIND_SHOT_IMAGE))
    close_ctx = shot_ctx(rules.KIND_SHOT_IMAGE, shot=_shot(shot_size="特写"))
    close = await build(rules.KIND_SHOT_IMAGE, close_ctx)

    assert wide["shot_size"] != close["shot_size"]
    assert wide["prompt"] != close["prompt"]
    # 特写类必须带局部说明，且局部说明**替换**"特写"二字，不是并列
    assert close["shot_size"].endswith("特写")
    assert "特写，" not in close["shot_size"]
    assert verify(rules.KIND_SHOT_IMAGE, close, close_ctx) == []


async def test_shot_size_disagreeing_with_the_storyboard_is_rejected() -> None:
    """成品词自己改了景别要被拦下来。"""
    ctx = shot_ctx(rules.KIND_SHOT_IMAGE)
    output = await build(rules.KIND_SHOT_IMAGE, ctx)
    output["shot_size"] = "中景"
    output["prompt"] = f"中景。{output['prompt']}"

    problems = rules.check_output(rules.KIND_SHOT_IMAGE, output, ctx)
    assert any("景别与分镜表不一致" in p for p in problems), problems


async def test_dropping_the_storyboard_angle_is_rejected() -> None:
    """用户改过的拍摄角度不能丢。"""
    ctx = shot_ctx(rules.KIND_SHOT_IMAGE)
    output = await build(rules.KIND_SHOT_IMAGE, ctx)
    output["prompt"] = str(output["prompt"]).replace("斜前方45度。", "")

    problems = rules.check_output(rules.KIND_SHOT_IMAGE, output, ctx)
    assert any("斜前方45度" in p for p in problems), problems


@pytest.mark.parametrize(("field", "label"), [("facing", "角色朝向"), ("behind", "身后背景")])
async def test_five_elements_are_all_required(field: str, label: str) -> None:
    """五要素缺一个就不合格。

    「身后背景」是原文点名最容易出错的一项：角色朝向一变背后空间完全不同，
    模型会套用一个固定背景。
    """
    ctx = shot_ctx(rules.KIND_SHOT_IMAGE)
    output = await build(rules.KIND_SHOT_IMAGE, ctx)
    assert output[field]

    output[field] = ""
    problems = rules.check_output(rules.KIND_SHOT_IMAGE, output, ctx)
    assert any(label in p for p in problems), problems


async def test_empty_shot_says_so_instead_of_leaving_facing_blank() -> None:
    """空镜的 `facing` 要写「无人物出场」，不是留空。"""
    ctx = shot_ctx(rules.KIND_SHOT_IMAGE, shot=_shot(character_refs=[]), characters=[])
    output = await build(rules.KIND_SHOT_IMAGE, ctx)

    assert output["facing"] == "无人物出场"
    assert verify(rules.KIND_SHOT_IMAGE, output, ctx) == []


async def test_shot_frame_uses_anchor_names_not_dict_reprs() -> None:
    """身后空间从锚点推演时取的是锚点名称，不是整个字典。"""
    ctx = shot_ctx(rules.KIND_SHOT_IMAGE)
    output = await build(rules.KIND_SHOT_IMAGE, ctx)

    assert "锈迹铁门" in output["behind"]
    assert "{'name'" not in output["behind"]
    assert "description" not in output["behind"]


# --------------------------------------------------------- C2：视频词的要求


async def test_video_prompt_ends_with_the_mandatory_line() -> None:
    """最后一行强制声明不可省略、不可移到别处。"""
    ctx = shot_ctx(rules.KIND_SHOT_VIDEO)
    output = await build(rules.KIND_SHOT_VIDEO, ctx)

    assert str(output["prompt"]).rstrip().endswith(rules.VIDEO_TAIL)
    assert verify(rules.KIND_SHOT_VIDEO, output, ctx) == []

    moved = dict(
        output,
        prompt=f"{rules.VIDEO_TAIL}\n{output['prompt'].replace(rules.VIDEO_TAIL, '')}",
    )
    assert rules.check_output(rules.KIND_SHOT_VIDEO, moved, ctx)


async def test_video_prompt_meets_the_cut_minimum() -> None:
    """无台词最少两个切镜，有台词最少三个且台词要逐字出现。"""
    silent_ctx = shot_ctx(rules.KIND_SHOT_VIDEO)
    silent = await build(rules.KIND_SHOT_VIDEO, silent_ctx)
    assert len(silent["cuts"]) >= 2
    assert verify(rules.KIND_SHOT_VIDEO, silent, silent_ctx) == []

    line = "有问题想问你。"
    spoken_ctx = shot_ctx(
        rules.KIND_SHOT_VIDEO, shot=_shot(dialogue=line, speaker_ref="guan_zhang")
    )
    spoken = await build(rules.KIND_SHOT_VIDEO, spoken_ctx)
    assert len(spoken["cuts"]) >= 3, "有台词的镜号最少三个切镜（台词特写法则）"
    assert line in spoken["prompt"], "台词必须逐字出现，不得改写或缩写"
    assert verify(rules.KIND_SHOT_VIDEO, spoken, spoken_ctx) == []


async def test_too_few_cuts_is_rejected() -> None:
    ctx = shot_ctx(rules.KIND_SHOT_VIDEO)
    output = await build(rules.KIND_SHOT_VIDEO, ctx)
    output["cuts"] = output["cuts"][:1]

    problems = rules.check_output(rules.KIND_SHOT_VIDEO, output, ctx)
    assert any("切镜数量不足" in p for p in problems), problems


async def test_two_cuts_on_one_line_is_rejected() -> None:
    """两个切镜挤在同一条里是原文点名的禁止项。"""
    ctx = shot_ctx(rules.KIND_SHOT_VIDEO)
    output = await build(rules.KIND_SHOT_VIDEO, ctx)
    merged = f"{output['cuts'][0]}{output['cuts'][1]}"
    output["cuts"] = [merged]
    output["prompt"] = str(output["prompt"]).replace(
        f"{output['prompt'].splitlines()[1]}", merged, 1
    )

    problems = rules.check_output(rules.KIND_SHOT_VIDEO, output, ctx)
    assert any("同一行" in p or "切镜数量不足" in p for p in problems), problems


async def test_storyboard_camera_move_and_sfx_survive() -> None:
    """运镜与音效是用户在分镜表上定过的，不能丢。"""
    ctx = shot_ctx(rules.KIND_SHOT_VIDEO, shot=_shot(camera_move="向左横移", sfx="门锁转动声"))
    output = await build(rules.KIND_SHOT_VIDEO, ctx)

    assert "向左横移" in output["prompt"]
    assert "门锁转动声" in output["prompt"]
    assert verify(rules.KIND_SHOT_VIDEO, output, ctx) == []


async def test_video_prompt_does_not_describe_subtitles_or_bgm() -> None:
    """「背景音乐」「字幕」只允许出现在最后那行强制声明里。"""
    ctx = shot_ctx(rules.KIND_SHOT_VIDEO)
    output = await build(rules.KIND_SHOT_VIDEO, ctx)

    assert str(output["prompt"]).count("背景音乐") == 1
    assert str(output["prompt"]).count("字幕") == 1

    output["prompt"] = f"{output['prompt']}\n画面下方配有字幕。"
    problems = rules.check_output(rules.KIND_SHOT_VIDEO, output, ctx)
    assert any("字幕" in p for p in problems), problems


# ------------------------------------------------- eval 样例也要过同一道校验


@pytest.mark.parametrize(
    ("kind", "schema_name"),
    [(k, SCHEMA_OF_KIND[k]) for k in rules.KINDS],
)
def test_eval_samples_satisfy_the_template_rules(kind: str, schema_name: str) -> None:
    """`SCHEMA_SAMPLES` 的四份样例不只是形状合法，还要过模板校验。

    样例是提示词作者最先照抄的东西。一份"形状对、内容不合格"的样例会教出
    一个同样不合格的提示词，而 `score_schemas` 只做 `model_validate`，
    单靠它拦不住。

    这里传空上下文：模板校验里与运行时数据有关的那几条（景别要对得上分镜
    表、档案配件不许省）在空上下文下自然跳过，剩下的**结构性**要求
    ——四格格名、三重否定、最后一行强制声明——正是样例该自证的部分。
    """
    sample = SCHEMA_SAMPLES[schema_name]
    schemas.resolve(schema_name).model_validate(sample)
    assert rules.check_output(kind, sample, {}) == []


# --------------------------------- B3：档案上的结构化外貌事实必须活到成品词
#
# 这一组是这次返工的核心。在这之前，"角色的发型服装有没有进最终提示词"
# 只由两个**模型自报**的字段（subject_identity / accessories）代表，而那两个
# 字段是模型自己填的——它把服装归纳成两个字、把发型整句删掉，自报字段一个字
# 不动，校验全绿。立绘是后续每一镜的比对基准，基准上少一件白衬衫，全片跟着少。


async def test_structured_appearance_facts_survive_into_the_prompt() -> None:
    """档案上写明的外貌事实，逐项都要出现在成品词里。"""
    ctx = character_ctx()
    output = await build(rules.KIND_CHARACTER, ctx)

    for fact in ("黑色短发", "额前碎发", "深灰西装外套", "白衬衫", "黑色皮鞋", "左手常握成拳"):
        assert fact in output["prompt"], f"档案上的「{fact}」没进成品词"
    assert verify(rules.KIND_CHARACTER, output, ctx) == []


async def test_a_dropped_appearance_fact_is_caught_even_with_a_clean_self_report() -> None:
    """只删掉发型这一处，两个自报字段保持自洽 —— 仍然要被拒。

    这是返工的判据本身：校验必须拿**上下文里的已知事实**去比对成品词，
    而不是信任模型对自己的声明。只查自报字段的话，这条用例会绿。
    """
    ctx = character_ctx()
    output = await build(rules.KIND_CHARACTER, ctx)
    output["prompt"] = str(output["prompt"]).replace("发型发色：黑色短发，额前碎发。", "")

    # 自报字段一个字都没动，仍然与档案自洽
    assert output["subject_identity"] == "日本"
    assert output["accessories"] == "警号铜牌"

    problems = rules.check_output(rules.KIND_CHARACTER, output, ctx)
    assert any("黑色短发" in p for p in problems), problems


async def test_a_generalised_appearance_fact_is_rejected() -> None:
    """把「深灰西装外套」归纳成「西装」也算丢事实。

    归纳看起来无害，但同一个角色下一镜会被归纳成另一个词，
    而扩散模型对"深灰西装外套"和"西装"画出来的不是同一件衣服。
    """
    ctx = character_ctx()
    output = await build(rules.KIND_CHARACTER, ctx)
    output["prompt"] = str(output["prompt"]).replace("深灰西装外套", "西装")

    problems = rules.check_output(rules.KIND_CHARACTER, output, ctx)
    assert any("深灰西装外套" in p for p in problems), problems


async def test_an_archive_fact_conflicting_with_the_template_is_an_explicit_error() -> None:
    """档案上写着背包 —— 显式报冲突，不偷偷取舍。

    照抄档案违反四铁律，按铁律删掉等于替用户改角色设定。两条都不能默默做，
    所以这里必须是一条说得清的错误，指向"回去修档案"。
    """
    ctx = character_ctx(character=_character(outfit="深灰西装外套，双肩背包"))
    output = await build(rules.KIND_CHARACTER, ctx)

    problems = rules.check_output(rules.KIND_CHARACTER, output, ctx)
    assert any("冲突" in p and "背包" in p for p in problems), problems
    # Mock 没把背包写进成品词，所以不该同时再报一条"提示词里出现了背包"
    assert "背包" not in output["prompt"]


async def test_a_fact_recorded_as_none_does_not_demand_the_word() -> None:
    """档案写「无」是"确实没有"，不该要求提示词里出现「无」这两个字。"""
    ctx = character_ctx(character=_character(accessories="无", distinctive="无"))
    output = await build(rules.KIND_CHARACTER, ctx)

    assert verify(rules.KIND_CHARACTER, output, ctx) == []


# ------------------------- 冻结的 appearance 是判据，旧 sheet 只是回落
#
# `character` 有两层：顶层来自 `current_state_json` 里的整份 sheet（上游重跑
# 一次就会变），`appearance` 来自 `character_profiles.appearance_json`——
# 冻结的、出图与相似度比对真正用的那一版。两层冲突时必须听冻结的，否则校验
# 会去要求提示词里出现一段**这张图根本不会照着画**的旧事实。


async def test_the_frozen_appearance_wins_over_a_stale_sheet() -> None:
    """冻结档案与旧 sheet 冲突：以冻结的那一份为准，旧值不许复活。"""
    ctx = character_ctx(character=_character(appearance={"hair": "白色长发，齐腰"}))
    output = await build(rules.KIND_CHARACTER, ctx)

    assert "白色长发，齐腰" in output["prompt"], "冻结的发型没进成品词"
    assert "黑色短发" not in output["prompt"], "旧 sheet 上的发型被复活了"
    assert verify(rules.KIND_CHARACTER, output, ctx) == []

    # 反面：模型照着旧 sheet 写就该被拒——它写的不是这张图要画的那个人
    stale = dict(
        output, prompt=str(output["prompt"]).replace("白色长发，齐腰", "黑色短发，额前碎发")
    )
    problems = rules.check_output(rules.KIND_CHARACTER, stale, ctx)
    assert any("白色长发" in p for p in problems), problems


async def test_an_explicitly_empty_frozen_fact_is_not_resurrected() -> None:
    """冻结档案上这一项是空的，就是空的——不回落到旧 sheet 把它补回来。

    `consistency._appearance` 冻的是**全部**结构化字段，没写的那几项会留成
    空串。所以"键存在但为空"与"键根本不存在"是两件事：前者是"冻结时这一项
    就没有"，拿旧 sheet 去补等于要求提示词写一段这张图不会照着画的事实；
    后者（存量档案、或只有非人类模板才用的那几项）才回落。
    """
    ctx = character_ctx(character=_character(appearance={"hair": "", "outfit": ""}))
    output = await build(rules.KIND_CHARACTER, ctx)

    facts = dict(rules.known_appearance(ctx["character"]))
    assert "发型发色" not in facts, "冻结成空的发型被旧 sheet 复活了"
    assert "服装造型" not in facts
    assert facts["鞋子"] == "黑色皮鞋", "键不存在的那几项仍然回落到档案本体"

    assert "黑色短发" not in output["prompt"]
    assert "深灰西装外套" not in output["prompt"]
    assert verify(rules.KIND_CHARACTER, output, ctx) == []


# ------------------------------------------- B5：摄影主轴与锚点描述必须落地


async def test_scene_prompt_keeps_the_camera_axis_as_the_composition_basis() -> None:
    """主轴三段逐字落地，并且被点明为构图依据。"""
    ctx = scene_ctx()
    output = await build(rules.KIND_SCENE, ctx)

    for fact in ("铁门外的路面", "朝向建筑正面", "红砖建筑正门石阶"):
        assert fact in output["prompt"], f"主轴的「{fact}」没进成品词"
    assert any(p in output["prompt"] for p in rules.AXIS_BASIS_PHRASES)
    assert verify(rules.KIND_SCENE, output, ctx) == []


@pytest.mark.parametrize("fact", ["铁门外的路面", "朝向建筑正面", "红砖建筑正门石阶"])
async def test_dropping_a_camera_axis_segment_is_rejected(fact: str) -> None:
    """主轴少一段，四格的"正面"就又由模型自己挑了。"""
    ctx = scene_ctx()
    output = await build(rules.KIND_SCENE, ctx)
    output["prompt"] = str(output["prompt"]).replace(fact, "")

    problems = rules.check_output(rules.KIND_SCENE, output, ctx)
    assert any(fact in p for p in problems), problems


async def test_scene_prompt_keeps_every_anchor_description() -> None:
    """锚点的名称和描述都要在：只给名称，模型不知道它长什么样、在哪。"""
    ctx = scene_ctx()
    output = await build(rules.KIND_SCENE, ctx)
    for anchor in ANCHORS:
        assert anchor["description"] in output["prompt"], anchor["name"]

    output["prompt"] = str(output["prompt"]).replace(ANCHORS[1]["description"], "")
    problems = rules.check_output(rules.KIND_SCENE, output, ctx)
    assert any(ANCHORS[1]["name"] in p for p in problems), problems


async def test_scene_prompt_is_titled_by_the_scene_not_the_last_anchor() -> None:
    """正文标题用的是**场景名**，不是最后一条锚点的名字。

    实证缺陷（2026-09-17）：`_scene_prompt` 里拼 `anchor_facts` 的推导式写的是
    `(name := ...)`，而推导式中的海象赋值绑定在**外层函数作用域**（PEP 572）。
    于是外层那个 `name = scene["name"]` 被就地改写成最后一条锚点的名字，正文首行
    从「资料馆门口概念图」变成「门柱铜牌概念图」。

    **`check_output` 抓不到它**：校验器查的是四格格名、三重否定、主轴三段、
    每条锚点的名称与描述——标题叫什么不在其中，所以那次产出 `verify()` 返回 `[]`，
    集成测试也全绿。能发现它的只有这一条断言，所以这条断言不能删。

    前置条件（场景名与最后一条锚点名不同）在用例里显式断言：哪天 fixture 漂成
    两者同名，这条用例会变成永远通过的空壳，那时该红的是前置条件而不是结论。
    """
    ctx = scene_ctx()
    scene_name = str(ctx["scene"]["name"])
    last_anchor = str(ctx["scene"]["fixed_references"][-1]["name"])
    assert scene_name != last_anchor, "fixture 前置条件失效：场景名与最后一条锚点同名"

    output = await build(rules.KIND_SCENE, ctx)
    first_line = str(output["prompt"]).splitlines()[0]

    assert first_line.startswith(f"{scene_name}概念图"), first_line
    assert not first_line.startswith(last_anchor), first_line

    # 改名不能以丢掉锚点为代价：名称与描述逐条仍在。
    for a in ANCHORS:
        assert f"{a['name']}：{a['description']}" in output["prompt"], a["name"]
        assert a["name"] in output["fixed_elements"], a["name"]
    assert verify(rules.KIND_SCENE, output, ctx) == []


async def test_scene_fallback_element_also_uses_the_scene_name() -> None:
    """凑元素用的「XX 的地面与墙面」同样取场景名。

    `key_elements` 为空时才走到这条回落分支，默认 fixture 覆盖不到它。它和标题
    读的是同一个变量，所以一起钉住——否则修好标题、回落分支仍可能被改回去。
    """
    ctx = scene_ctx(scene=_scene(key_elements=[], fixed_references=[]))
    scene_name = str(ctx["scene"]["name"])
    output = await build(rules.KIND_SCENE, ctx)

    assert f"{scene_name}的地面与墙面" in output["fixed_elements"], output["fixed_elements"]
    assert verify(rules.KIND_SCENE, output, ctx) == []


async def test_shot_frame_anchor_facts_do_not_leak_a_name_variable() -> None:
    """首帧的锚点推导式同样不占用 `name` 这个名字。

    这个函数今天没有外层 `name` 可被覆盖，所以改名前后产出逐字相同——这条用例
    钉的是产出，不是写法：锚点名称与描述成对落地，身后背景那句用的仍是场景名。
    """
    ctx = shot_ctx(rules.KIND_SHOT_IMAGE)
    scene_name = str(ctx["scene"]["name"])
    output = await build(rules.KIND_SHOT_IMAGE, ctx)

    for a in ANCHORS:
        assert f"{a['name']}（{a['description']}）" in output["prompt"], a["name"]
    assert output["behind"].startswith(f"身后是{scene_name}的"), output["behind"]
    assert verify(rules.KIND_SHOT_IMAGE, output, ctx) == []


async def test_scene_prompt_rejects_a_lighting_state_it_did_not_reference() -> None:
    """没被引用的那个光照状态漏进来也不合格。"""
    ctx = scene_ctx()
    output = await build(rules.KIND_SCENE, ctx)
    output["prompt"] = f"{output['prompt']}\n{OTHER_LIGHTING['description']}"

    problems = rules.check_output(rules.KIND_SCENE, output, ctx)
    assert any("傍晚" in p for p in problems), problems


# ------------------------------------- 首帧：身后空间要留下从锚点推演的证据


async def test_shot_frame_keeps_the_whole_axis_and_every_anchor_description() -> None:
    """主轴三段与每一条锚点描述都要落地——与四视图同一个标准。

    完整的参照事实是这一镜的**参照系**，不是"画面里要出现的东西清单"：
    写全它不要求每件参照物都入画，哪一部分入画由分镜表的景别与角度决定。
    """
    ctx = shot_ctx(rules.KIND_SHOT_IMAGE)
    output = await build(rules.KIND_SHOT_IMAGE, ctx)

    for fact in ("铁门外的路面", "朝向建筑正面", "红砖建筑正门石阶"):
        assert fact in output["prompt"], f"主轴的「{fact}」没进成品词"
    for anchor in ANCHORS:
        assert anchor["description"] in output["prompt"], anchor["name"]
    assert verify(rules.KIND_SHOT_IMAGE, output, ctx) == []


@pytest.mark.parametrize("index", [0, 1])
async def test_shot_frame_dropping_any_anchor_description_is_rejected(index: int) -> None:
    """删掉任意一条锚点描述都要被拒，**包括第二条**。

    放松成"至少落地一条"等于让模型自己挑留哪几条，而它挑剩下的那几条
    下一镜就会换个样子——同一个房间又开始漂，那正是锚点要防的事。
    """
    ctx = shot_ctx(rules.KIND_SHOT_IMAGE)
    output = await build(rules.KIND_SHOT_IMAGE, ctx)
    output["prompt"] = str(output["prompt"]).replace(str(ANCHORS[index]["description"]), "")

    problems = rules.check_output(rules.KIND_SHOT_IMAGE, output, ctx)
    assert any(str(ANCHORS[index]["name"]) in p for p in problems), problems


async def test_shot_frame_dropping_the_axis_far_end_is_rejected() -> None:
    """远景末端同样不能丢。

    只写站位和朝向，模型不知道这条轴指向哪里，身后空间照样是编的——
    而"这一镜不一定沿主轴拍"不是丢掉它的理由：拍到哪一段由景别与角度决定。
    """
    ctx = shot_ctx(rules.KIND_SHOT_IMAGE)
    output = await build(rules.KIND_SHOT_IMAGE, ctx)
    output["prompt"] = str(output["prompt"]).replace("红砖建筑正门石阶", "")

    problems = rules.check_output(rules.KIND_SHOT_IMAGE, output, ctx)
    assert any("红砖建筑正门石阶" in p for p in problems), problems


async def test_shot_frame_must_keep_the_referenced_lighting_state() -> None:
    """这一镜引用的那个光照状态的描述不能丢。"""
    ctx = shot_ctx(rules.KIND_SHOT_IMAGE)
    output = await build(rules.KIND_SHOT_IMAGE, ctx)
    output["prompt"] = str(output["prompt"]).replace(str(LIGHTING["description"]), "")

    problems = rules.check_output(rules.KIND_SHOT_IMAGE, output, ctx)
    assert any("光照状态" in p for p in problems), problems
