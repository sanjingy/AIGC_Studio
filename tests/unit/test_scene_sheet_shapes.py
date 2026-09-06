"""场景档案的两处结构化：固定参照物两段式 + 多光照状态。

—— 为什么这些用例值得单独一个文件 ——

这次改动把 `SceneSheet` 上两个**已经有数据**的字段换了形状。换形状本身不难，
难的是"库里那些老数据怎么办"：`PATCH .../outputs/scenes` 会拿**现行 schema**
去校验早就存下的产出（`extra="forbid"`），少一步兼容翻译，所有存量项目的
场景档案立刻变成不可编辑——而且报错信息是"多了一个 lighting 字段"，
没人看得出这是兼容问题。

所以这里逐条打靶的是三件事：

1. 存量的扁平形状仍然能过校验，且**原文一个字都不丢**
2. 光照**永远解析得出一个状态**——没有默认状态等于漏填的镜头行为未定义
3. 模型给出的畸形数据（重名状态、指向不存在的默认值）不会让整份产出失败

零 Provider 调用、零数据库：只跑 pydantic。
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from agents.schemas import (
    DEFAULT_LIGHTING_NAME,
    MIGRATED_NAME_CHARS,
    SceneSheet,
    SceneSheets,
    Storyboard,
    coerce_fixed_references,
    coerce_lighting_states,
)

LEGACY_ANCHOR = "锈迹斑斑的滑动铁门在画面正前方，右扇下缘锈穿一个洞"

BASE: dict[str, Any] = {
    "ref": "gate",
    "name": "资料馆门口",
    "time_slot": "上午",
    "setting": "爬满爬山虎的院墙",
    "key_elements": ["铁门"],
    "camera_axis": {"position": "铁门外", "facing": "朝向建筑正面", "far_end": "石阶"},
}


def _legacy(**over: Any) -> dict[str, Any]:
    """迁移之前落库的那个形状。"""
    return {**BASE, "lighting": "上午均匀自然日光", "fixed_references": [LEGACY_ANCHOR], **over}


def _modern(**over: Any) -> dict[str, Any]:
    return {
        **BASE,
        "lighting_states": [
            {"name": "上午", "description": "均匀自然日光自左上方射入"},
            {"name": "夜巡灯", "description": "门柱顶灯自上方直射"},
        ],
        "default_lighting": "上午",
        "fixed_references": [
            {"name": "锈迹铁门", "description": "画面正前方的双开滑动铁门，右扇下缘锈穿一个洞"}
        ],
        **over,
    }


# ------------------------------------------------------------------ 存量兼容


def test_legacy_flat_scene_still_validates() -> None:
    """扁平形状必须仍然能过校验。

    过不了的后果不是"报个错"，是所有存量项目的场景档案变成不可编辑——
    字段级编辑拿现行 schema 校验的就是这些老数据。
    """
    sheet = SceneSheet.model_validate(_legacy())
    assert sheet.ref == "gate"


def test_legacy_lighting_becomes_one_state_named_default() -> None:
    """单个 `lighting` → 一个名为「默认」的状态，原文进 description。

    不给存量场景凭空编出四个时段：老数据里只有一种光，迁完仍然只有一种，
    行为与迁移前逐字相同。
    """
    sheet = SceneSheet.model_validate(_legacy())
    assert [s.name for s in sheet.lighting_states] == [DEFAULT_LIGHTING_NAME]
    assert sheet.lighting_states[0].description == "上午均匀自然日光"
    assert sheet.lighting_states[0].origin == "migrated"
    assert sheet.default_lighting == DEFAULT_LIGHTING_NAME


def test_legacy_anchor_keeps_the_original_text_in_full() -> None:
    """名称是从描述截出来的**副本**，不是替换——原文必须完整保留。

    这条是迁移可逆的前提：降级时只取 `description` 就能逐字还原回去。
    """
    sheet = SceneSheet.model_validate(_legacy())
    anchor = sheet.fixed_references[0]
    assert anchor.description == LEGACY_ANCHOR, "存量锚点的原文被改了"
    assert anchor.name == LEGACY_ANCHOR[:MIGRATED_NAME_CHARS]
    assert anchor.origin == "migrated"


def test_legacy_key_is_dropped_not_carried_along() -> None:
    """`lighting` 翻译完就不能再留在产出里。

    留着就有两份真相：界面改了 `lighting`，出图读的是 `lighting_states`，
    用户看到"改了没生效"。
    """
    dumped = SceneSheet.model_validate(_legacy()).model_dump()
    assert "lighting" not in dumped


def test_modern_shape_is_untouched() -> None:
    """新形状原样通过，不能被兼容逻辑改写。"""
    sheet = SceneSheet.model_validate(_modern())
    assert [s.name for s in sheet.lighting_states] == ["上午", "夜巡灯"]
    assert all(s.origin == "authored" for s in sheet.lighting_states)
    assert sheet.fixed_references[0].name == "锈迹铁门"
    assert sheet.fixed_references[0].origin == "authored"


# ------------------------------------------------------------ 默认状态一定存在


def test_scene_with_one_state_is_legal() -> None:
    """只声明一个状态是合法的，且它就是默认。

    室内不分时、剧本里光线没变化的场景就是这种。提示词明确要求这时**只**
    声明一个，机械地把一天四段列全会造出没有任何镜头会引用的死状态。
    """
    sheet = SceneSheet.model_validate(
        _modern(
            lighting_states=[{"name": "常态", "description": "顶灯与台灯为主"}],
            default_lighting="常态",
        )
    )
    assert len(sheet.lighting_states) == 1
    assert sheet.default_lighting == "常态"


def test_explicit_empty_state_list_is_repaired_not_rejected() -> None:
    """显式给了空列表，也要补出一个默认状态，而不是校验失败。

    这条容易被写反。直觉上"空列表"该被 `min_length=1` 拒掉，但拒掉的代价
    落在**存量数据**上：库里真的有 `fixed_references: []` 这种行，模型也真的
    会漏填整个字段。让一份场景档案因此不可编辑，换来的只是一个更早的报错，
    而更早的报错在这里没有价值——没有任何下游需要"这个场景没有光"这个信息。

    所以 `min_length=1` 在这里的作用不是拦输入，是**声明一条不变量**：
    落到下游的 `lighting_states` 一定非空。兼容层负责让它成立。
    """
    sheet = SceneSheet.model_validate({**BASE, "lighting_states": [], "default_lighting": ""})
    assert len(sheet.lighting_states) == 1
    assert sheet.default_lighting == sheet.lighting_states[0].name


def test_the_non_empty_invariant_is_still_declared() -> None:
    """反向守护：`min_length=1` 这道声明不能被谁顺手删掉。

    **不能用"构造一个空列表看它报不报错"来测**：`SceneSheet(**kw)` 和
    `model_validate(kw)` 走的是同一条路，兼容层在前面就把空列表补上了，
    那样写出来的用例永远是绿的，测的是兼容层不是约束。
    读 schema 是唯一能直接看见这道声明的地方。
    """
    schema = SceneSheet.model_json_schema()["properties"]["lighting_states"]
    assert schema["minItems"] == 1, "光照状态非空这道不变量被去掉了"


def test_too_many_lighting_states_is_rejected() -> None:
    """上限**不**由兼容层修补——它拦的是"把一天四段外加节气全列一遍"。

    这条和 `min_length` 相反：补一个默认状态是无损的，而砍掉多余的状态
    要替模型决定砍哪几个。宁可报错让它重来。
    """
    with pytest.raises(ValidationError):
        SceneSheet.model_validate(
            _modern(
                lighting_states=[
                    {"name": f"状态{i}", "description": f"第 {i} 种光"} for i in range(7)
                ],
                default_lighting="状态0",
            )
        )


def test_default_lighting_is_filled_in_when_the_model_omits_it() -> None:
    """模型漏填默认状态 → 落到第一个，不留空。

    留空意味着漏填 `lighting_ref` 的镜头行为未定义，那正是这次要消灭的东西。
    """
    sheet = SceneSheet.model_validate(_modern(default_lighting=""))
    assert sheet.default_lighting == "上午"


def test_default_lighting_pointing_at_an_unknown_state_is_repaired() -> None:
    """默认状态指向一个没声明过的名字 → 落到第一个。

    模型自己写错、或者用户在字段级编辑里把某个状态改了名，都会造出这种数据。
    """
    sheet = SceneSheet.model_validate(_modern(default_lighting="黄昏"))
    assert sheet.default_lighting == "上午"


def test_duplicate_state_names_are_dropped() -> None:
    """同名状态只留第一个。

    按名字解析永远取第一个匹配，第二个再也选不到——留在列表里只会在门③
    和分镜表上多一个点了没用的选项。
    """
    sheet = SceneSheet.model_validate(
        _modern(
            lighting_states=[
                {"name": "上午", "description": "第一份"},
                {"name": "上午", "description": "第二份，永远选不到"},
            ],
            default_lighting="上午",
        )
    )
    assert [s.description for s in sheet.lighting_states] == ["第一份"]


# ------------------------------------------------------------------ 纯函数层


def test_coerce_accepts_a_half_filled_anchor() -> None:
    """只有名称、或只有描述的锚点都要补全，不能整条丢掉。"""
    out = coerce_fixed_references([{"name": "船头铜铃"}, {"description": "篷布右后角一块补丁"}])
    assert out[0] == {"name": "船头铜铃", "description": "船头铜铃", "origin": "migrated"}
    assert out[1]["name"] == "篷布右后角一块补丁"[:MIGRATED_NAME_CHARS]
    assert out[1]["description"] == "篷布右后角一块补丁"


def test_coerce_drops_only_the_entirely_empty_entries() -> None:
    out = coerce_fixed_references(["", "  ", {"name": "", "description": ""}, "铁门在正前方"])
    assert [o["description"] for o in out] == ["铁门在正前方"]


def test_coerce_lighting_always_returns_a_resolvable_default() -> None:
    """三种输入都要给出"至少一个状态 + 一个指得到实处的默认名"。"""
    for design in ({}, {"lighting": ""}, {"lighting_states": []}):
        states, default = coerce_lighting_states(design)
        assert states, f"{design} 没给出任何状态"
        assert default in {s["name"] for s in states}, f"{design} 的默认状态指向空处"


# --------------------------------------------------------------- 分镜引用光照


def _storyboard(**shot_over: Any) -> dict[str, Any]:
    return {
        "nodes": [{"index": 1, "scene_ref": "gate", "summary": "门口"}],
        "shots": [
            {
                "index": 1,
                "node_index": 1,
                "scene_ref": "gate",
                "shot_size": "全景",
                "content": "主角站在铁门外",
                **shot_over,
            }
        ],
    }


def test_shot_lighting_ref_defaults_to_empty() -> None:
    """存量分镜没有这个字段，不能因此校验失败。

    留空在运行时等于"用该场景的默认状态"，也是分镜提示词教模型的
    安全写法（拿不准就留空）。
    """
    board = Storyboard.model_validate(_storyboard())
    assert board.shots[0].lighting_ref == ""


def test_shot_lighting_ref_is_kept_verbatim() -> None:
    """引用只存名字。

    **schema 层不校验它指得到实处**：`Storyboard` 校验时手里没有
    `SceneSheets`，跨产出的引用在这里判不了。合法性在合成提示词那一层
    解决（未知引用降级到默认 + 记 warning），理由见
    `consistency/compose.py::resolve_lighting`。
    """
    board = Storyboard.model_validate(_storyboard(lighting_ref="夜巡灯"))
    assert board.shots[0].lighting_ref == "夜巡灯"


def test_shot_may_reference_a_state_no_scene_declares() -> None:
    """写了一个不存在的状态名，分镜产出本身仍然合法。

    反过来做（schema 报错）就是让一镜写错名字毁掉整份 600 镜的分镜表。
    """
    board = Storyboard.model_validate(_storyboard(lighting_ref="根本没声明过"))
    assert board.shots[0].lighting_ref == "根本没声明过"


def test_scene_sheets_wrapper_still_validates() -> None:
    """整份产出（含 era）走一遍，确认外层没被改坏。"""
    sheets = SceneSheets.model_validate({"era": "现代", "scenes": [_legacy(), _modern(ref="hall")]})
    assert [s.ref for s in sheets.scenes] == ["gate", "hall"]
