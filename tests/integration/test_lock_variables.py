"""画风目录与项目级锁定变量（ADR-036 第 3 条 + ADR-037 门①）。

两条规则值得单独一个文件盯着，因为它们错了都**不会立刻报错**：

1. **画风目录走数据不走代码常量。** 在这之前画风是 `consistency/service.py`
   里一个写死的 `STYLE_PRESETS` dict，加一种画风就要发版——和"价格写成常量"
   是同一类问题（CLAUDE.md 硬规则第 1 条）。这里断言目录真的从库里来。
2. **画风一旦冻结就不能再换 key。** `ensure_style` 是"已存在就不覆盖"的，
   改了 key 也不会生效，界面却会显示改成功了。一次看起来成功的无效操作
   比一个明确的拒绝糟得多——用户会以为全片都按新画风重出了。
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from tests.conftest import advance_to_gate

pytestmark = pytest.mark.integration

P = "/api/v1/projects"

NOVEL = "把这篇小说做成 5 分钟悬疑漫剧，主角是一名被调职的刑警。"


async def _project(client: AsyncClient, title: str = "lock-vars") -> str:
    return str((await client.post(P, json={"title": title})).json()["id"])


async def _lock(client: AsyncClient, pid: str) -> dict:
    r = await client.get(f"{P}/{pid}/lock-variables")
    assert r.status_code == 200, r.text
    return dict(r.json())


# ---------------------------------------------------------------- 画风目录


async def test_style_catalog_comes_from_the_database(alice: AsyncClient) -> None:
    """目录是数据。断言它有多条、每条三套描述词齐全、且带排序用的展示信息。"""
    pid = await _project(alice)
    options = (await _lock(alice, pid))["style_options"]

    assert len(options) >= 2, "内置目录至少有两条，全空说明种子数据没进去"
    keys = [o["key"] for o in options]
    assert len(keys) == len(set(keys))
    for option in options:
        assert option["name"] and option["description"], f"{option['key']} 没有可展示的名字或说明"
        assert option["character_tokens"], f"{option['key']} 缺人物版描述词"
        assert option["scene_tokens"], f"{option['key']} 缺场景版描述词"
        assert option["video_tokens"], f"{option['key']} 缺视频版描述词"


async def test_scene_tokens_say_the_scene_is_empty(alice: AsyncClient) -> None:
    """场景版必须明写这是一个**空**场景（ADR-036 第 3 条）。

    场景参考图是同一场景后续所有镜头的空间基准。基准图里画进一个人，
    那个人就会被当成这个空间的一部分带进每一镜。

    判据接受**两种写法**，因为目录里现在有两代数据：四道门那一批
    （`e4b7c9d21f38`）写的是「空场景无人物」，ADR-038 迁进来的超哥原版八条
    （`a8c3e91d6402`）写的是「高质量实景空场景」「高质量空场景」。原版那批
    是**逐字照抄源词库**的（源文 sha256 由
    `tests/unit/test_chaoge_catalog_migration.py` 盯着），不能为了凑一个
    关键词去改它——"源模板逐字照抄"是比这条断言更硬的规则。

    所以这里钉的是**语义**而不是某一个词：场景版必须出现"空场景"或"无人物"
    之一。两种写法都真的表达了"这里没有人"，而漏掉这件事的那种退化
    （场景版被写成人物版的复制品）两个词都不会出现，仍然拦得住。
    """
    pid = await _project(alice)
    for option in (await _lock(alice, pid))["style_options"]:
        tokens = option["scene_tokens"]
        assert any(word in tokens for word in ("空场景", "无人物")), (
            f"{option['key']} 的场景版没写明空场景：{tokens}"
        )


async def test_a_project_with_no_lock_row_still_gets_the_options(alice: AsyncClient) -> None:
    """还没走到门① 的项目读锁定变量要返 200 + 空值 + 完整可选项，不是 404。

    "还没选过"是一个合法状态，不是资源不存在。返 404 会让界面在门① 之前
    没法展示"可以选哪些画风"。
    """
    pid = await _project(alice, "never-advanced")
    lock = await _lock(alice, pid)

    assert lock["style_key"] == ""
    assert lock["era"] == ""
    assert lock["adaptation_mode"] == "adapt"
    assert lock["confirmed_at"] is None
    assert lock["style_options"], "可选项不能因为还没选过就不给"
    assert lock["adaptation_options"] == ["adapt", "rewrite"]


# ---------------------------------------------------------------- 改锁定变量


async def test_partial_update_leaves_the_other_fields_alone(alice: AsyncClient) -> None:
    """只传一个字段就只改那一个。

    要求前端把全部字段读出来再整份传回去，两个标签页同开就会互相覆盖
    （与 `ProjectModelPreferenceIn` 是同一条理由）。
    """
    pid = await _project(alice)
    await alice.post(f"{P}/{pid}/advance?to_gate=true", json={"user_input": NOVEL})

    r = await alice.put(f"{P}/{pid}/lock-variables", json={"era": "民国"})
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["era"] == "民国"

    r = await alice.put(f"{P}/{pid}/lock-variables", json={"adaptation_mode": "rewrite"})
    assert r.status_code == 200, r.text
    second = r.json()
    assert second["adaptation_mode"] == "rewrite"
    assert second["era"] == "民国", "改改编模式把时代背景冲掉了"


async def test_unknown_style_key_is_rejected(alice: AsyncClient) -> None:
    """存一个目录里没有的画风 key = 让选择静默失效。

    `ensure_style` 解析不到会报错，而那时用户已经走到角色出图那一步了，
    错误出现的位置离原因很远。
    """
    pid = await _project(alice)
    r = await alice.put(f"{P}/{pid}/lock-variables", json={"style_key": "no_such_style"})
    assert r.status_code >= 400, r.text
    assert "no_such_style" in r.text


async def test_unknown_adaptation_mode_is_rejected(alice: AsyncClient) -> None:
    pid = await _project(alice)
    r = await alice.put(f"{P}/{pid}/lock-variables", json={"adaptation_mode": "plagiarize"})
    assert r.status_code == 422, r.text


async def test_style_can_be_chosen_before_the_profile_is_frozen(alice: AsyncClient) -> None:
    """门① 上换画风是正常操作——风格档案还没建出来。"""
    pid = await _project(alice)
    await alice.post(f"{P}/{pid}/advance?to_gate=true", json={"user_input": NOVEL})

    options = (await _lock(alice, pid))["style_options"]
    target = next(o["key"] for o in options if o["key"] != options[0]["key"])

    r = await alice.put(f"{P}/{pid}/lock-variables", json={"style_key": target})
    assert r.status_code == 200, r.text
    assert r.json()["style_key"] == target


async def test_the_chosen_style_is_the_one_that_gets_frozen(alice: AsyncClient) -> None:
    """门① 选的画风必须真的进到风格档案里。

    存下来却不被 `ensure_style` 读走，用户选的画风就只是一个显示值，
    全片按缺省画风出图——而且要到看图时才发现。
    """
    org_id = uuid.UUID((await alice.get("/api/v1/auth/me")).json()["org_id"])
    pid = await _project(alice)
    await alice.post(f"{P}/{pid}/advance?to_gate=true", json={"user_input": NOVEL})

    options = (await _lock(alice, pid))["style_options"]
    target = next(o for o in options if o["key"] != options[0]["key"])
    r = await alice.put(f"{P}/{pid}/lock-variables", json={"style_key": target["key"]})
    assert r.status_code == 200, r.text

    # 一路跑到角色阶段之后，风格档案才会被建出来
    await advance_to_gate(alice, pid, "anchors")

    from apps.api.core.db import session_scope
    from apps.api.modules.consistency import service as consistency

    async with session_scope() as db:
        style = await consistency.get_style(db, org_id=org_id, project_id=uuid.UUID(pid))

    assert style is not None
    assert style.style_key == target["key"]
    assert style.character_tokens == target["character_tokens"]
    assert style.scene_tokens == target["scene_tokens"]


async def test_changing_the_style_after_freezing_is_refused(alice: AsyncClient) -> None:
    """风格档案建出来之后改画风要被明确拒绝，而不是假装成功。

    `ensure_style` 已存在就不覆盖，所以改 key 不会有任何效果。让这次调用
    返 200 等于告诉用户"换好了"，而全片仍然是旧画风。
    """
    pid = await _project(alice)
    await advance_to_gate(alice, pid, "anchors", user_input=NOVEL)

    lock = await _lock(alice, pid)
    other = next(o["key"] for o in lock["style_options"] if o["key"] != lock["style_key"])

    r = await alice.put(f"{P}/{pid}/lock-variables", json={"style_key": other})
    assert r.status_code == 409, r.text
    assert "重出" in r.text, "拒绝的理由要说清代价，不能只说不行"


async def test_era_can_still_be_changed_after_the_style_is_frozen(alice: AsyncClient) -> None:
    """时代背景与改编模式在冻结之后仍可改：它们只影响还没跑的阶段。

    把它们和画风一起锁死，用户发现人种判错之后就只能新建项目重来。
    """
    pid = await _project(alice)
    await advance_to_gate(alice, pid, "anchors", user_input=NOVEL)

    r = await alice.put(f"{P}/{pid}/lock-variables", json={"era": "近未来"})
    assert r.status_code == 200, r.text
    assert r.json()["era"] == "近未来"
