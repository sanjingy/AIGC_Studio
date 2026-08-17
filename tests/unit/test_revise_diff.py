"""修订的改动 diff。

这是"局部改，不是整体重跑"这条规则的**验证手段**。
模型说它只改了一处、diff 说它改了七处时，以 diff 为准——
所以 diff 本身必须可信。
"""

from __future__ import annotations

from typing import Any

from apps.api.modules.agent.revise import (
    REVISABLE_ROLES,
    REVISE_CONTRACT,
    build_revise_input,
    diff_fields,
    downstream_of,
)


def test_no_change_reports_nothing() -> None:
    doc = {"title": "雾港迷案", "acts": [{"index": 1, "title": "开场"}]}
    assert diff_fields(doc, dict(doc)) == []


def test_scalar_change_is_named() -> None:
    assert diff_fields({"title": "旧"}, {"title": "新"}) == ["title"]


def test_list_items_are_identified_not_just_counted() -> None:
    """按 ref/index 标识元素，而不是报"acts 变了"。

    列表整体报变化等于没报——用户想知道的是哪个角色被改了。
    """
    old: dict[str, Any] = {
        "characters": [
            {"ref": "lin_shu", "name": "林舒", "build": "偏瘦"},
            {"ref": "wang", "name": "王腾", "build": "魁梧"},
        ]
    }
    new: dict[str, Any] = {
        "characters": [
            {"ref": "lin_shu", "name": "林舒", "build": "结实精干"},
            {"ref": "wang", "name": "王腾", "build": "魁梧"},
        ]
    }
    assert diff_fields(old, new) == ["characters[lin_shu]"]


def test_added_and_removed_are_distinguished() -> None:
    old = {"characters": [{"ref": "a"}, {"ref": "b"}]}
    new = {"characters": [{"ref": "a"}, {"ref": "c"}]}
    assert diff_fields(old, new) == ["characters[b] 删除", "characters[c] 新增"]


def test_falls_back_to_position_without_an_identifier() -> None:
    assert diff_fields({"tags": ["x", "y"]}, {"tags": ["x", "z"]}) == ["tags[1]"]


def test_top_level_key_added_or_removed() -> None:
    assert diff_fields({"a": 1}, {"a": 1, "b": 2}) == ["b"]
    assert diff_fields({"a": 1, "b": 2}, {"a": 1}) == ["b"]


def test_report_is_capped() -> None:
    """改了 200 个镜头时列 200 条没人会看，只会淹掉真正重要的那几条。"""
    old = {"shots": [{"index": i, "content": "旧"} for i in range(50)]}
    new = {"shots": [{"index": i, "content": "新"} for i in range(50)]}
    report = diff_fields(old, new)
    assert len(report) == 13
    assert report[-1].startswith("…另有")


def test_downstream_is_marked_stale() -> None:
    """改了上游，下游是基于旧版生成的。

    不说这件事，用户会以为改完就同步了，直到成片里对不上。
    """
    assert downstream_of("screenplay") == ["characters", "scenes", "storyboard"]
    assert downstream_of("storyboard") == []
    assert REVISABLE_ROLES == ("plot_index", "screenplay", "characters", "scenes", "storyboard")


def test_revise_input_carries_the_whole_current_output() -> None:
    """当前产出整份带上，模型才能保证改动前后自洽。

    只给被改的那一块，改了角色性别，台词里的称呼不会跟着改。
    """
    text = build_revise_input({"title": "雾港迷案", "acts": []}, "把主角改成女性")
    assert "雾港迷案" in text
    assert "把主角改成女性" in text


def test_revise_input_does_not_end_by_telling_it_to_preserve() -> None:
    """末句必须重申"以修改要求为准"，不能以"原样保留"收尾。

    这条是用真金白银换来的。上线的第一版把「其余字段原样保留」放在
    整条消息最后，模型对着"故事完全不对，请重写"原样返回了当前产出，
    一个字没改（2026-08-18 实测，deepseek-chat）。
    两条指令冲突时靠后的赢——"保留"排在"重写"后面就是这个结果。

    "只改相关部分"那层意思放在系统契约里，不跟用户的具体要求抢末句。
    """
    text = build_revise_input({"title": "雾港迷案", "acts": []}, "把主角改成女性")
    tail = text[-120:]

    assert "以修改要求为准" in tail
    assert "原样保留" not in tail, "末句不能叫它保留，那会压过用户的修改要求"
    assert not text.rstrip().endswith("}"), "也不能以那份 JSON 收尾"
    assert text.index("【修改要求】") < text.index("【当前产出】")


def test_revise_contract_states_the_task_is_a_revision() -> None:
    """系统契约把任务性质说清楚。

    注意：对照实验里它**单独不足以**修复原样返回（旧消息 + 有契约
    照样返回 0 处改动）。决定性的是消息结构。保留它是因为它把任务
    说对了，别指望它兜底。
    """
    assert "修订已有产出" in REVISE_CONTRACT
    assert "禁止原样返回当前产出" in REVISE_CONTRACT
    assert "优先级" in REVISE_CONTRACT
