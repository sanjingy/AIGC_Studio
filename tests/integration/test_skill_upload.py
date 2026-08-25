"""用户上传 Skill（ADR-026）。

三条主线：

1. **合法 spec 存下来，并且把派生信息算出来**（阶段数、门、缺失的 Agent）
2. **非法 spec 也要 201 入库**——丢掉它用户只会得到"传了没反应"的黑洞，
   他需要在列表里看见这一条并读到错在哪
3. **跨租户 404**，且列表里看不见别人 org 的东西

另有一条贯穿的断言：`runtime_wired` 恒为 False。ADR-026 明确要求界面
如实告知"运行时没接线"，这句话由后端给——哪天真接上了，前端不用改。
"""

from __future__ import annotations

import uuid

import pytest
import yaml
from httpx import AsyncClient

from skills import registry as skill_registry

pytestmark = pytest.mark.integration

BASE = "/api/v1/skills"


def _minimal_spec(skill_id: str = "skill.demo_line.v1", name: str = "演示生产线") -> str:
    """一份刚好合法的 spec：一个 agent 阶段 + 一道 required 的门。

    直接照着 `skills/spec.py` 的必填项写，不去 import builtin 那份——
    内置那份会随主线演进改动，拿它当测试夹具会让这里跟着无关变更红。

    agent 阶段**不带 review**：只有 generate 阶段有审查策略，
    给 agent 阶段加一个反而会被 spec 判非法。
    """
    return yaml.safe_dump(
        {
            "id": skill_id,
            "name": name,
            "status": "draft",
            "route": "NOVEL_TO_ANIME",
            "stages": [
                {"id": "plot_index", "name": "情节目录", "kind": "agent", "role": "story"},
                {
                    "id": "confirm_script",
                    "name": "确认剧本",
                    "kind": "gate",
                    "gate": "script",
                    "required": True,
                },
            ],
        },
        allow_unicode=True,
    )


async def _upload(client: AsyncClient, spec_yaml: str) -> dict:
    r = await client.post(BASE, json={"spec_yaml": spec_yaml})
    assert r.status_code == 201, r.text
    return dict(r.json())


# ---------------------------------------------------------------- 合法 spec


async def test_valid_spec_is_stored_with_derived_fields(alice: AsyncClient) -> None:
    body = await _upload(alice, _minimal_spec())

    assert body["status"] == "valid"
    assert body["validation_errors"] is None
    assert body["name"] == "演示生产线"
    # 版本从 id 的 .vN 后缀取，不让用户另外填一遍
    assert body["version"] == "v1"
    assert body["skill_id"] == "skill.demo_line.v1"
    assert body["route"] == "NOVEL_TO_ANIME"
    assert body["stage_count"] == 2
    assert body["gates"] == ["script"]
    # ADR-026：运行时没接线，这个标志恒为假
    assert body["runtime_wired"] is False


async def test_pinned_agent_that_does_not_exist_is_reported_but_not_fatal(
    alice: AsyncClient,
) -> None:
    """spec 钉死一个不存在的 Agent：提醒，但不判 spec 非法。

    Agent 注册表是热加载的——一个第三方 Agent 临时加载失败，
    不该让引用它的整条生产线定义变成非法。理由见
    `skills/registry.missing_agents` 的 docstring。
    """
    raw = yaml.safe_load(_minimal_spec())
    raw["stages"][0]["agent"] = "story.does_not_exist.v9"
    body = await _upload(alice, yaml.safe_dump(raw, allow_unicode=True))

    assert body["status"] == "valid"
    assert body["missing_agents"] == ["story.does_not_exist.v9"]


async def test_spec_referencing_a_real_agent_has_no_missing(alice: AsyncClient) -> None:
    known = next(iter(skill_registry.agent_registry.registry().specs))
    raw = yaml.safe_load(_minimal_spec())
    raw["stages"][0]["agent"] = known
    body = await _upload(alice, yaml.safe_dump(raw, allow_unicode=True))

    assert body["missing_agents"] == []


# ---------------------------------------------------------------- 非法 spec


async def test_invalid_spec_is_still_recorded_with_its_errors(alice: AsyncClient) -> None:
    """没有 required 门的 spec 不合法，但照样入库。

    一条全自动、任何一步都不给人看的生产线，出问题只能在成片阶段发现，
    那时候钱已经花完了——所以 spec 层就拦掉。
    """
    raw = yaml.safe_load(_minimal_spec())
    raw["stages"] = [raw["stages"][0]]  # 去掉门
    body = await _upload(alice, yaml.safe_dump(raw, allow_unicode=True))

    assert body["status"] == "invalid"
    assert body["validation_errors"]
    assert "门" in body["validation_errors"]
    # 派生字段一律为空：spec 都没通过，算出来的东西没有意义
    assert body["skill_id"] is None
    assert body["gates"] == []
    # 名字仍然从原文里捞出来了，用户要能认出是自己传的哪一份
    assert body["name"] == "演示生产线"


async def test_export_path_escape_is_rejected(alice: AsyncClient) -> None:
    """导出路径不许向上穿越。这条会往用户磁盘任意位置写，必须拦死。"""
    raw = yaml.safe_load(_minimal_spec())
    raw["stages"][0]["export"] = "../../etc"
    body = await _upload(alice, yaml.safe_dump(raw, allow_unicode=True))

    assert body["status"] == "invalid"
    assert "导出路径" in body["validation_errors"]


async def test_non_yaml_is_rejected_outright(alice: AsyncClient) -> None:
    """连 YAML 都不是就没有可展示的内容，这种才是真的失败。"""
    r = await alice.post(BASE, json={"spec_yaml": "\tthis: [is: not: yaml"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "skill.spec.unreadable"


async def test_scalar_toplevel_is_rejected(alice: AsyncClient) -> None:
    r = await alice.post(BASE, json={"spec_yaml": "just a string"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "skill.spec.unreadable"


async def test_oversized_spec_is_rejected(alice: AsyncClient) -> None:
    # 上限按字节算，用中文撑过去以确认没有按字符数误判
    payload = "name: " + "锚" * (skill_registry.MAX_SPEC_BYTES // 2)
    r = await alice.post(BASE, json={"spec_yaml": payload})
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "skill.spec.too_large"


# ---------------------------------------------------------------- 列表 / 原文 / 删除


async def test_list_and_read_spec_round_trip(alice: AsyncClient) -> None:
    spec_yaml = _minimal_spec(skill_id="skill.round_trip.v2", name="往返")
    created = await _upload(alice, spec_yaml)

    rows = (await alice.get(BASE)).json()["items"]
    assert created["id"] in [r["id"] for r in rows]

    # 原文原样存着：校验规则以后会变，重新校验要拿得到原文
    r = await alice.get(f"{BASE}/{created['id']}/spec")
    assert r.status_code == 200
    assert r.json()["spec_yaml"] == spec_yaml


async def test_delete_removes_it_from_the_list(alice: AsyncClient) -> None:
    created = await _upload(alice, _minimal_spec(skill_id="skill.to_delete.v1"))

    assert (await alice.delete(f"{BASE}/{created['id']}")).status_code == 204

    rows = (await alice.get(BASE)).json()["items"]
    assert created["id"] not in [r["id"] for r in rows]
    # 软删的东西也不能再取回原文
    assert (await alice.get(f"{BASE}/{created['id']}/spec")).status_code == 404


async def test_unknown_id_is_404(alice: AsyncClient) -> None:
    assert (await alice.get(f"{BASE}/{uuid.uuid4()}/spec")).status_code == 404


# ---------------------------------------------------------------- 跨租户


async def test_other_org_cannot_see_or_touch_it(alice: AsyncClient, bob: AsyncClient) -> None:
    created = await _upload(alice, _minimal_spec(skill_id="skill.alices_line.v1"))

    # 别人 org 的列表里不该出现
    assert created["id"] not in [r["id"] for r in (await bob.get(BASE)).json()["items"]]

    # 直接猜到 id 也拿不到——404 不是 403，403 会确认资源存在
    assert (await bob.get(f"{BASE}/{created['id']}/spec")).status_code == 404
    assert (await bob.delete(f"{BASE}/{created['id']}")).status_code == 404
