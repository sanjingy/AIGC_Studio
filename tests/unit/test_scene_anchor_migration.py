"""场景锚点/光照那次迁移的形状转换，逐条打靶。

—— 为什么不是"跑一遍 alembic 就算了" ——

`alembic upgrade → downgrade -1 → upgrade` 只能告诉你"没报错"。它不会告诉你
**存量锚点的原文有没有被改掉一个字**——而那恰恰是这次迁移唯一不可挽回的
风险：`fixed_references` 里的句子是用户/模型写下的原始内容，迁移把它拆成
两段的过程中若截错了，没有任何地方还留着原样。

所以这里直接测迁移模块里的纯函数：升级一遍、降级一遍，断言**逐字相等**。

零数据库：`_up_scene` / `_down_scene` 都不碰 `op`，按文件路径加载即可。
"""

from __future__ import annotations

import importlib.util
import pathlib
import types
from typing import Any

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
MIGRATION = (
    REPO / "migrations" / "versions" / "f1c8d05e37a2_structured_scene_anchors_lighting_states.py"
)

LEGACY_ANCHORS = ["铁门在画面正前方，右扇下缘锈穿一个巴掌大的洞", "门柱牌子在铁门右侧"]
LEGACY_LIGHTING = "上午均匀自然日光，阴影清晰短小"


@pytest.fixture(scope="module")
def m() -> types.ModuleType:
    """按文件路径加载迁移模块。

    Alembic 的版本文件不是可 import 的包路径（文件名带 revision 前缀）。
    加载只执行模块顶层，不跑任何 DDL——`upgrade()` 要 op 上下文才动得了。
    """
    spec = importlib.util.spec_from_file_location("wm_scene_migration", MIGRATION)
    assert spec is not None and spec.loader is not None, f"加载不了迁移文件 {MIGRATION}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_scene() -> dict[str, Any]:
    return {
        "time_slot": "上午",
        "setting": "爬满爬山虎的院墙",
        "lighting": LEGACY_LIGHTING,
        "camera_axis": {"position": "铁门外", "facing": "朝向建筑正面", "far_end": "石阶"},
        "fixed_references": list(LEGACY_ANCHORS),
        "key_elements": ["铁门"],
    }


# ------------------------------------------------------------------ 升级


def test_flat_anchors_keep_their_text_in_full(m: types.ModuleType) -> None:
    """存量锚点的原文整句进 `description`，一个字都不改。"""
    out = m._up_scene(_legacy_scene())
    assert [a["description"] for a in out["fixed_references"]] == LEGACY_ANCHORS
    assert all(a["origin"] == "migrated" for a in out["fixed_references"])


def test_derived_names_are_a_copy_not_a_replacement(m: types.ModuleType) -> None:
    """名称从描述前若干字截出来，是副本——原文仍然完整。"""
    out = m._up_scene(_legacy_scene())
    first = out["fixed_references"][0]
    assert first["name"] == LEGACY_ANCHORS[0][: m.MIGRATED_NAME_CHARS]
    assert first["description"].startswith(first["name"])


def test_flat_lighting_becomes_one_default_state(m: types.ModuleType) -> None:
    out = m._up_scene(_legacy_scene())
    assert out["lighting_states"] == [
        {"name": m.DEFAULT_LIGHTING_NAME, "description": LEGACY_LIGHTING, "origin": "migrated"}
    ]
    assert out["default_lighting"] == m.DEFAULT_LIGHTING_NAME
    assert "lighting" not in out, "旧键留着就有两份真相"


def test_empty_lighting_still_produces_a_state(m: types.ModuleType) -> None:
    """连 `lighting` 都是空串的行也要有默认状态。

    没有默认状态 = 漏填 `lighting_ref` 的镜头行为未定义，那正是这次要
    消灭的东西。空描述注入的内容与迁移前（`lighting` 为空串）一致。
    """
    out = m._up_scene({**_legacy_scene(), "lighting": ""})
    assert len(out["lighting_states"]) == 1
    assert out["lighting_states"][0]["description"] == ""
    assert out["default_lighting"] == m.DEFAULT_LIGHTING_NAME


def test_already_structured_scene_is_idempotent(m: types.ModuleType) -> None:
    """升级一次和升级两次结果必须一样。

    `upgrade → downgrade -1 → upgrade` 要跑得通，这条是前提；生产上
    重跑一次迁移也不该把两段式再包一层。
    """
    once = m._up_scene(_legacy_scene())
    assert m._up_scene(once) == once


# ------------------------------------------------------------------ 降级


def test_round_trip_restores_legacy_text_verbatim(m: types.ModuleType) -> None:
    """升级再降级，存量数据必须**逐字**回到原样。

    这是验收标准第 2 条（存量数据不丢原文）唯一硬碰硬的证明。
    """
    original = _legacy_scene()
    back = m._down_scene(m._up_scene(original))
    assert back["fixed_references"] == LEGACY_ANCHORS
    assert back["lighting"] == LEGACY_LIGHTING
    assert "lighting_states" not in back
    assert "default_lighting" not in back


def test_authored_anchors_are_joined_rather_than_dropped(m: types.ModuleType) -> None:
    """作者写的两段式降级时拼成"名称：描述"。

    旧 schema 里没有第二段的归宿，**这一半是有损的**——但拼起来至少不丢字。
    降级用于回滚一次刚出问题的部署，不是用于来回切换。
    """
    scene = {
        "lighting_states": [
            {"name": "夜巡灯", "description": "门柱顶灯直射", "origin": "authored"}
        ],
        "default_lighting": "夜巡灯",
        "fixed_references": [
            {"name": "船头铜铃", "description": "挂在篷杆前端，绿锈斑驳", "origin": "authored"}
        ],
    }
    back = m._down_scene(scene)
    assert back["fixed_references"] == ["船头铜铃：挂在篷杆前端，绿锈斑驳"]
    assert back["lighting"] == "门柱顶灯直射"


def test_downgrade_picks_the_default_state_not_the_first(m: types.ModuleType) -> None:
    """降级只剩一个 `lighting`，取的必须是**默认**状态。

    取第一个会让一个以夜戏为主、默认状态排在第二位的场景，回滚之后
    突然变成白天。
    """
    scene = {
        "lighting_states": [
            {"name": "上午", "description": "自然日光"},
            {"name": "夜巡灯", "description": "门柱顶灯直射"},
        ],
        "default_lighting": "夜巡灯",
    }
    assert m._down_scene(scene)["lighting"] == "门柱顶灯直射"


# ------------------------------------------------------- current_state_json


def _state() -> dict[str, Any]:
    return {
        "stage": "storyboard",
        "scenes": {"era": "现代", "scenes": [{"ref": "gate", **_legacy_scene()}]},
        "storyboard": {
            "nodes": [{"index": 1, "scene_ref": "gate", "summary": "门口"}],
            "shots": [
                {"index": 1, "node_index": 1, "scene_ref": "gate", "content": "全景"},
                {"index": 2, "node_index": 1, "scene_ref": "unknown", "content": "空镜"},
            ],
        },
    }


def test_existing_shots_point_at_the_default_state(m: types.ModuleType) -> None:
    """存量镜头一律显式指向默认状态，而不是留空。

    留空在运行时行为一样（都落到默认），但显式写进去，用户在分镜表上看到的
    是"这一镜用【默认】光"而不是一个空格——空格会被当成还没填的待办。
    """
    out = m._rewrite_state(_state(), up=True)
    assert out is not None
    shots = out["storyboard"]["shots"]
    assert shots[0]["lighting_ref"] == m.DEFAULT_LIGHTING_NAME
    # 引用了一个场景档案里没有的 ref：填不出名字就留空，运行时照样落到
    # 默认状态。不能瞎填一个别的场景的状态名。
    assert shots[1]["lighting_ref"] == ""


def test_state_round_trip_restores_shots_and_scenes(m: types.ModuleType) -> None:
    original = _state()
    up = m._rewrite_state(original, up=True)
    assert up is not None
    back = m._rewrite_state(up, up=False)
    assert back is not None
    assert back["scenes"] == original["scenes"]
    assert back["storyboard"] == original["storyboard"]


def test_state_without_scenes_or_storyboard_is_skipped(m: types.ModuleType) -> None:
    """还没跑到场景那一步的项目一行都不用改，跳过它的 UPDATE。"""
    assert m._rewrite_state({"stage": "routing"}, up=True) is None


def test_already_migrated_state_is_skipped(m: types.ModuleType) -> None:
    """跑过一次的项目第二次没有任何改动 → 不发 UPDATE。"""
    up = m._rewrite_state(_state(), up=True)
    assert up is not None
    assert m._rewrite_state(up, up=True) is None
