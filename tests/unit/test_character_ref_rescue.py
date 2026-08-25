"""`CharacterSheets` 的 ref 兜底。

真机上"绯色冴子"这类中日文人名会被模型原样塞进 `ref`，撞上
`^[a-z][a-z0-9_]{1,30}$` 直接校验失败，三次重试全废，角色阶段 500。
提示词已经把格式写清楚了，但提示词管不住模型的所有输出——
这一层保证格式错只降级成一条 warning，不再让整个阶段卡死。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.schemas import REF_PATTERN, CharacterSheets, fallback_character_ref


def _sheet(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "ref": "zhu_jue",
        "name": "主角",
        "kind": "人类",
        "camp": "正派",
        "identity": "被调职的刑警",
        "personality": ["隐忍"],
        "age_range": "30 出头",
        "hair": "黑色短发",
        "eyes": "深褐色",
        "face": "轮廓分明",
        "build": "中等身高，结实精干",
        "outfit": "深灰西装外套",
    }
    base.update(overrides)
    return base


def test_chinese_name_ref_is_rescued() -> None:
    """原来会抛 ValidationError 的输入现在要能解析出合法 ref。"""
    parsed = CharacterSheets.model_validate(
        {"characters": [_sheet(ref="绯色冴子", name="绯色冴子")]}
    )

    ref = parsed.characters[0].ref
    assert REF_PATTERN.match(ref), ref
    assert parsed.characters[0].name == "绯色冴子", "只改 ref，不动人名"


def test_valid_refs_are_left_alone() -> None:
    """一个角色出错不能连累其他角色，合法 ref 必须原样保留。"""
    parsed = CharacterSheets.model_validate(
        {
            "characters": [
                _sheet(ref="zhang_san", name="张三"),
                _sheet(ref="绯色冴子", name="绯色冴子"),
                _sheet(ref="guan_zhang", name="馆长"),
            ]
        }
    )

    refs = [c.ref for c in parsed.characters]
    assert refs[0] == "zhang_san"
    assert refs[2] == "guan_zhang"
    assert REF_PATTERN.match(refs[1])
    assert len(set(refs)) == 3


def test_fallback_ref_is_stable_across_positions() -> None:
    """同一个人名跨次生成必须得到同一个 ref。

    一致性引擎按 ref 认人。位置下标会让同一个角色这次 char_1 下次 char_2，
    于是库里出现两份档案，原来那份还会被别人的外貌覆盖。
    """
    first = CharacterSheets.model_validate(
        {
            "characters": [
                _sheet(ref="绯色冴子", name="绯色冴子"),
                _sheet(ref="zhang_san", name="张三"),
            ]
        }
    )
    # 第二次生成多了一个角色，绯色冴子的位置往后挪了
    second = CharacterSheets.model_validate(
        {
            "characters": [
                _sheet(ref="zhang_san", name="张三"),
                _sheet(ref="li_si", name="李四"),
                _sheet(ref="绯色冴子", name="绯色冴子"),
            ]
        }
    )

    assert first.characters[0].ref == second.characters[2].ref


def test_same_name_twice_still_gets_distinct_refs() -> None:
    """同名角色出现两次不能被合并成一个人。"""
    parsed = CharacterSheets.model_validate(
        {
            "characters": [
                _sheet(ref="绯色冴子", name="绯色冴子"),
                _sheet(ref="绯色冴子", name="绯色冴子"),
            ]
        }
    )

    refs = [c.ref for c in parsed.characters]
    assert len(set(refs)) == 2
    assert all(REF_PATTERN.match(r) for r in refs)


@pytest.mark.parametrize("bad", ["", "   ", "1abc", "a", "ZHANG SAN", "冴子-A", "x" * 40])
def test_all_illegal_shapes_are_rescued(bad: str) -> None:
    parsed = CharacterSheets.model_validate({"characters": [_sheet(ref=bad, name="冴子")]})
    assert REF_PATTERN.match(parsed.characters[0].ref)


def test_case_and_whitespace_are_normalized_not_hashed() -> None:
    """大小写和空格是格式问题，不是"没法罗马化"，别浪费掉已有的可读 ref。"""
    parsed = CharacterSheets.model_validate(
        {"characters": [_sheet(ref="  Zhang_San ", name="张三")]}
    )
    assert parsed.characters[0].ref == "zhang_san"


def test_missing_ref_falls_back_too() -> None:
    sheet = _sheet(name="冴子")
    sheet.pop("ref")
    parsed = CharacterSheets.model_validate({"characters": [sheet]})
    assert REF_PATTERN.match(parsed.characters[0].ref)


def test_nameless_character_falls_back_to_position() -> None:
    """连人名都没有时只剩位置可用。"""
    assert fallback_character_ref("", 0) == "char_1"
    assert fallback_character_ref("   ", 4) == "char_5"


def test_rescue_does_not_swallow_other_errors() -> None:
    """兜底只管 ref。别的字段缺了照样要报错，不能把校验变成摆设。"""
    sheet = _sheet(ref="绯色冴子", name="绯色冴子")
    sheet.pop("identity")
    with pytest.raises(ValidationError):
        CharacterSheets.model_validate({"characters": [sheet]})
