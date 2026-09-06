"""四道门那次迁移里的两处**判据**，必须和运行时的表对得上。

迁移文件是一次性执行的代码，跑过就不会再跑。所以它里面写死的判据一旦
和运行时的阶段图漂移，没有任何东西会报错——只会在下一次有人照着它
写迁移时把错误抄一遍。这个文件把那两处判据钉到运行时的权威来源上：

1. `_BEFORE_PLAN_GATE`：哪些 stage 值算"还没到门①"。这个集合决定了
   哪些存量项目会被补一行 `origin='migrated'` 的锁定变量。多算一个，
   一批本该补行的项目漏补；少算一个，一批还没到门口的项目被提前
   补上一行缺省画风，然后门① 打开时显示成"已经选好了"。
2. `_DEFAULT_STYLE_KEY` 必须真的在种子数据里，否则回填出来的
   `style_key` 指向一条不存在的目录项，`ensure_style` 解析不到会报错，
   而那时用户已经走到角色出图那一步了。

零 Provider 调用、零数据库：只读迁移模块里的常量与运行时的表。
"""

from __future__ import annotations

import importlib.util
import pathlib
import types

import pytest

from apps.api.modules.agent import orchestrator

REPO = pathlib.Path(__file__).resolve().parents[2]
MIGRATION = REPO / "migrations" / "versions" / "e4b7c9d21f38_four_gates_style_catalog_lock_vars.py"


@pytest.fixture(scope="module")
def migration() -> types.ModuleType:
    """按文件路径加载迁移模块。

    Alembic 的版本文件不是一个可 import 的包路径（文件名带 revision 前缀），
    所以走 spec_from_file_location。加载它只是读常量，不执行任何 DDL——
    `upgrade()` / `downgrade()` 都要 op 上下文才跑得起来。
    """
    spec = importlib.util.spec_from_file_location("four_gates_migration", MIGRATION)
    assert spec is not None and spec.loader is not None, f"加载不了迁移文件 {MIGRATION}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stages_before(gate_stage: str) -> set[str]:
    """从 `_NEXT` 沿着阶段图往前走，得出 `gate_stage` **之前**的全部阶段。

    从表推而不是手写清单：手写的清单在加阶段时不会自己更新，而那正是
    这个文件想拦住的漂移。
    """
    before: set[str] = set()
    stage = "routing"
    while stage != gate_stage:
        before.add(stage)
        stage = orchestrator._NEXT[stage]
    return before


def test_before_plan_gate_matches_the_stage_graph(migration: types.ModuleType) -> None:
    """迁移里的"还没到门①"集合 = 阶段图里 `await_plan` 之前的阶段 + 对应旧名。

    旧名要一起算：库里存着 `story` 的项目其实停在情节目录，也在门① 之前。
    漏了它，那批项目会被当成"已越过门①"补一行 migrated，而它们马上就要
    真的走到门① 前面，于是门① 打开时显示的是一份从没被确认过、
    却已经存在的画风。
    """
    graph_before = _stages_before("await_plan")
    legacy_before = {old for old, new in orchestrator._LEGACY_STAGES.items() if new in graph_before}
    assert set(migration._BEFORE_PLAN_GATE) == graph_before | legacy_before


def test_stages_past_the_plan_gate_are_not_in_that_set(migration: types.ModuleType) -> None:
    """反向断言：门① 之后的阶段一个都不能落进"还没到门①"里。

    `visual`（旧名，翻译成 characters）是最容易搞错的一个——它看起来像
    一个很早的阶段名，实际上已经在门① 之后。
    """
    past = set(orchestrator._NEXT) - _stages_before("await_plan")
    legacy_past = {
        old
        for old, new in orchestrator._LEGACY_STAGES.items()
        if new not in _stages_before("await_plan")
    }
    assert not (past | legacy_past) & set(migration._BEFORE_PLAN_GATE)
    assert "visual" not in migration._BEFORE_PLAN_GATE
    assert "done" not in migration._BEFORE_PLAN_GATE


def test_default_style_key_exists_in_the_seed_data(migration: types.ModuleType) -> None:
    """回填用的缺省画风必须真的在种子数据里。

    指向一条不存在的目录项时，`ensure_style` 会在角色出图那一步才报错——
    离原因很远的地方。
    """
    keys = {row[0] for row in migration._STYLES}
    assert migration._DEFAULT_STYLE_KEY in keys


def test_every_seeded_style_has_all_three_token_sets(migration: types.ModuleType) -> None:
    """三套描述词一套都不能空（ADR-036 第 3 条）。

    空的那一套会让对应的那类产物拿到空风格词——画风漂移，且没有任何
    地方会报错，只有用户看出来"这两张不像一部片"。
    """
    for row in migration._STYLES:
        key, character_tokens, scene_tokens, video_tokens = row[0], row[4], row[5], row[6]
        assert character_tokens.strip(), f"{key} 缺人物版描述词"
        assert scene_tokens.strip(), f"{key} 缺场景版描述词"
        assert video_tokens.strip(), f"{key} 缺视频版描述词"


def test_seeded_scene_tokens_say_the_scene_is_empty(migration: types.ModuleType) -> None:
    """场景版必须明写"无人物"。

    场景参考图是同一场景后续所有镜头的**空间基准**，基准图里画进一个人，
    那个人就会被当成这个空间的一部分带进每一镜。这条要求是 ADR-036
    第 3 条把描述词拆成三套的直接理由，写在种子数据里才算落地。
    """
    for row in migration._STYLES:
        key, scene_tokens = row[0], row[5]
        assert "无人物" in scene_tokens, f"{key} 的场景版没写明空场景"


def test_seeded_style_keys_are_unique(migration: types.ModuleType) -> None:
    """key 是唯一索引，重复会让迁移本身在 bulk_insert 上炸掉。"""
    keys = [row[0] for row in migration._STYLES]
    assert len(keys) == len(set(keys))
