"""structured scene anchors and named lighting states

WM：`SceneSheet` 的两处只做了一半的字段，这次补全，数据侧一并迁移。

—— 改的是什么 ——

1. **固定参照物从扁平字符串变成两段式**：`["铁门在画面正前方"]` →
   `[{"name": ..., "description": ..., "origin": ...}]`。名称是给人和给下游
   引用的把手，描述是给出图模型复现用的。合成一句话两头不讨好。

2. **单个 `lighting` 变成一组具名光照状态**：`"上午自然日光"` →
   `lighting_states: [{"name": "默认", "description": "上午自然日光", ...}]`
   + `default_lighting: "默认"`。同一个地点在一部剧里会在不同时刻反复出现，
   一个字段只有两种结局：全场景共用一种光，或者每镜自由发挥。

3. **镜头引用光照状态**：`StoryboardShot.lighting_ref`。存量镜头一律指向
   默认状态——不留空是有意的，见下。

—— 为什么用 Python 循环而不是一条 UPDATE ——

要改的东西藏在三层 JSONB 里（`projects.current_state_json` → `scenes` →
`scenes[]` → `fixed_references[]`），而且转换本身带条件分支（已经是新形状
的不能再包一层）。写成 `jsonb_set` + `jsonb_array_elements` 的嵌套 SQL 大概
是四十行没人看得懂、也没人敢改的东西。这张表的行数是"项目数"量级，
一次性脚本用 Python 循环读改写，可读性换到的代价可以忽略。

—— 存量数据怎么保原文（验收标准第 2 条）——

**扁平字符串整句进 `description`，一个字都不改。** 名称是从它前 12 个字
**截出来的副本**，不是替换——原文仍然完整地在 `description` 里。所以
`downgrade` 能把 `origin='migrated'` 的那些逐字还原回去（只取 description），
`upgrade → downgrade → upgrade` 之后存量场景的锚点文本与迁移前完全相同。

作者写的（`origin='authored'`）在降级时拼成 `名称：描述`——那一层信息在
旧 schema 里没有归宿，**这一半是有损的**，和 `e4b7c9d21f38` 降级丢掉
`scene_tokens` 是同一类：降级用于回滚一次刚出问题的部署，不是用于来回切换。

—— 为什么存量镜头一律指向默认状态，而不是留空 ——

留空在运行时同样会落到默认状态（`compose.resolve_lighting`），行为一样。
但显式写进去，用户在分镜表上看到的是"这一镜用【默认】光"，而不是一个
空格；空格会让人以为这是个还没填的待办。这条与迁移给存量项目补
`project_lock_variables` 行、而不是等运行时兜底，是同一个判断。

Revision ID: f1c8d05e37a2
Revises: e4b7c9d21f38
Create Date: 2026-09-06 12:00:00.000000

"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "f1c8d05e37a2"
down_revision: str | None = "e4b7c9d21f38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 与 `agents/schemas.py` 的同名常量保持一致。**这里刻意不 import 它**：
# 迁移脚本必须在它写下的那一刻被冻结——`agents.schemas` 半年后改了名称上限，
# 这份历史迁移的行为不该跟着变，否则同一条 revision 在不同时间跑出不同结果，
# "重放一遍迁移得到同样的库"就不成立了。这是 Alembic 的通行做法，
# 也是本仓库既有迁移不 import 业务模型的原因。
MIGRATED_NAME_CHARS = 12
DEFAULT_LIGHTING_NAME = "默认"


# ------------------------------------------------------------------ 形状转换


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _up_fixed_references(value: Any) -> list[dict[str, str]]:
    """扁平字符串 → 两段式。已经是两段式的原样通过。"""
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            name = _text(item.get("name"))
            description = _text(item.get("description"))
            if not name and not description:
                continue
            origin = _text(item.get("origin"))
            if not description:
                description, origin = name, origin or "migrated"
            if not name:
                name, origin = description[:MIGRATED_NAME_CHARS], origin or "migrated"
            out.append(
                {
                    "name": name,
                    "description": description,
                    "origin": origin if origin in ("authored", "migrated") else "authored",
                }
            )
            continue
        if text := _text(item):
            # 原文整句进 description，名称是它的前若干字，是副本不是替换。
            out.append(
                {
                    "name": text[:MIGRATED_NAME_CHARS],
                    "description": text,
                    "origin": "migrated",
                }
            )
    return out


def _down_fixed_references(value: Any) -> list[str]:
    """两段式 → 扁平字符串。

    `migrated` 的只取 description——它就是升级前的原文，逐字还原。
    `authored` 的拼 `名称：描述`，那是这条路径上唯一能做到的不丢字。
    """
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            if text := _text(item):
                out.append(text)
            continue
        name = _text(item.get("name"))
        description = _text(item.get("description"))
        if not description:
            if name:
                out.append(name)
            continue
        if _text(item.get("origin")) == "migrated" or not name:
            out.append(description)
        else:
            out.append(f"{name}：{description}")
    return out


def _up_lighting(scene: dict[str, Any]) -> tuple[list[dict[str, str]], str]:
    """单个 `lighting` → 一个名为「默认」的状态。已有状态列表的原样通过。"""
    raw = scene.get("lighting_states")
    states: list[dict[str, str]] = []
    seen: set[str] = set()
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                name = _text(item.get("name"))
                description = _text(item.get("description"))
                origin = _text(item.get("origin"))
            else:
                name, description, origin = "", _text(item), "migrated"
            if not name and not description:
                continue
            if not name:
                name, origin = description[:MIGRATED_NAME_CHARS], origin or "migrated"
            if name in seen:
                continue
            seen.add(name)
            states.append(
                {
                    "name": name,
                    "description": description,
                    "origin": origin if origin in ("authored", "migrated") else "authored",
                }
            )

    if not states:
        states = [
            {
                "name": DEFAULT_LIGHTING_NAME,
                "description": _text(scene.get("lighting")),
                "origin": "migrated",
            }
        ]
        seen = {DEFAULT_LIGHTING_NAME}

    default = _text(scene.get("default_lighting"))
    if default not in seen:
        default = states[0]["name"]
    return states, default


def _up_scene(scene: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in scene.items() if k != "lighting"}
    states, default = _up_lighting(scene)
    out["lighting_states"] = states
    out["default_lighting"] = default
    if "fixed_references" in scene:
        out["fixed_references"] = _up_fixed_references(scene.get("fixed_references"))
    return out


def _down_scene(scene: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in scene.items() if k not in ("lighting_states", "default_lighting")}
    states = scene.get("lighting_states")
    description = ""
    if isinstance(states, list) and states:
        default = _text(scene.get("default_lighting"))
        chosen = next(
            (s for s in states if isinstance(s, dict) and _text(s.get("name")) == default),
            states[0],
        )
        description = _text(chosen.get("description")) if isinstance(chosen, dict) else ""
    out["lighting"] = description
    if "fixed_references" in scene:
        out["fixed_references"] = _down_fixed_references(scene.get("fixed_references"))
    return out


def _default_lighting_by_ref(state: dict[str, Any]) -> dict[str, str]:
    """场景 ref → 它的默认光照状态名。给存量镜头回填 `lighting_ref` 用。"""
    scenes = state.get("scenes")
    rows = scenes.get("scenes") if isinstance(scenes, dict) else None
    if not isinstance(rows, list):
        return {}
    out: dict[str, str] = {}
    for scene in rows:
        if not isinstance(scene, dict):
            continue
        if ref := _text(scene.get("ref")):
            out[ref] = _text(scene.get("default_lighting"))
    return out


# ---------------------------------------------------------------- 逐行改写


def _rewrite_state(state: dict[str, Any], *, up: bool) -> dict[str, Any] | None:
    """一份 `current_state_json`。没有任何改动时返回 None，跳过这行的 UPDATE。"""
    scenes_block = state.get("scenes")
    board = state.get("storyboard")
    if not isinstance(scenes_block, dict) and not isinstance(board, dict):
        return None

    out = dict(state)
    changed = False

    if isinstance(scenes_block, dict) and isinstance(scenes_block.get("scenes"), list):
        rewritten = [
            (_up_scene(s) if up else _down_scene(s)) if isinstance(s, dict) else s
            for s in scenes_block["scenes"]
        ]
        if rewritten != scenes_block["scenes"]:
            out["scenes"] = {**scenes_block, "scenes": rewritten}
            changed = True

    if isinstance(board, dict) and isinstance(board.get("shots"), list):
        defaults = _default_lighting_by_ref(out) if up else {}
        shots: list[Any] = []
        for shot in board["shots"]:
            if not isinstance(shot, dict):
                shots.append(shot)
                continue
            if up:
                if _text(shot.get("lighting_ref")):
                    shots.append(shot)
                    continue
                shots.append({**shot, "lighting_ref": defaults.get(_text(shot.get("scene_ref")), "")})
            else:
                shots.append({k: v for k, v in shot.items() if k != "lighting_ref"})
        if shots != board["shots"]:
            out["storyboard"] = {**board, "shots": shots}
            changed = True

    return out if changed else None


def _migrate(*, up: bool) -> None:
    bind = op.get_bind()

    # ---- scene_profiles.spatial_json ----
    rows = bind.execute(
        sa.text("SELECT id, spatial_json FROM scene_profiles WHERE spatial_json IS NOT NULL")
    ).all()
    for row_id, spatial in rows:
        if not isinstance(spatial, dict):
            continue
        rewritten = _up_scene(spatial) if up else _down_scene(spatial)
        if rewritten == spatial:
            continue
        bind.execute(
            sa.text(
                "UPDATE scene_profiles SET spatial_json = CAST(:doc AS jsonb) WHERE id = :id"
            ).bindparams(doc=json.dumps(rewritten, ensure_ascii=False), id=row_id)
        )

    # ---- projects.current_state_json ----
    rows = bind.execute(
        sa.text(
            "SELECT id, current_state_json FROM projects WHERE current_state_json IS NOT NULL"
        )
    ).all()
    for row_id, state in rows:
        if not isinstance(state, dict):
            continue
        rewritten = _rewrite_state(state, up=up)
        if rewritten is None:
            continue
        bind.execute(
            sa.text(
                "UPDATE projects SET current_state_json = CAST(:doc AS jsonb) WHERE id = :id"
            ).bindparams(doc=json.dumps(rewritten, ensure_ascii=False), id=row_id)
        )


def upgrade() -> None:
    _migrate(up=True)


def downgrade() -> None:
    _migrate(up=False)
