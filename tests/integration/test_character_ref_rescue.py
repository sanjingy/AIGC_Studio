"""中日文人名不能再把角色阶段整个打死。

真机复现：模型给"绯色冴子"输出的 `ref` 就是"绯色冴子"本身，撞上
`CharacterSheet.ref` 的正则后三次重试全废（重试不带纠错反馈，模型
每次都犯同一个错），`runner.run_agent()` 抛 AppError，`advance()` 不接，
整个请求 500，项目永远卡在角色阶段。

这里让 Mock 产出一份"中文 ref + 正常 ref 混着"的角色档案，
断言链路照常推进，且落进一致性引擎的 ref 全都合法。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from agents.schemas import REF_PATTERN
from apps.api.modules.agent import llm
from tests.conftest import advance_to_gate, approve_gate

pytestmark = pytest.mark.integration

P = "/api/v1/projects"

SOURCE = (
    "寺田聪站在锈迹斑斑的铁门前。馆长绯色冴子一袭白衣，黑色长发。把这篇小说做成 5 分钟悬疑漫剧。"
)


def _sheet(ref: str, name: str) -> dict[str, Any]:
    return {
        "ref": ref,
        "name": name,
        "kind": "人类",
        "camp": "中立",
        "identity": "资料馆的人",
        "personality": ["冷峻"],
        "age_range": "年龄难辨",
        "hair": "黑色长发",
        "eyes": "瞳仁大而深",
        "face": "五官精致",
        "build": "修长纤细",
        "outfit": "一袭白衣",
    }


def _sheets_with_chinese_ref(_seed: int, _user: str) -> dict[str, Any]:
    """模型把人名原样当 ref 输出——真机上就是这个样子。"""
    return {
        "characters": [
            _sheet("寺田聪", "寺田聪"),
            _sheet("guan_zhang", "馆长"),
            _sheet("绯色冴子", "绯色冴子"),
        ]
    }


async def test_chinese_ref_does_not_break_the_character_stage(
    alice: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.api.modules.consistency import service as consistency
    from apps.api.modules.project import service as project_service

    monkeypatch.setitem(llm._BUILDERS, "CharacterSheets", _sheets_with_chinese_ref)

    org_id = uuid.UUID((await alice.get("/api/v1/auth/me")).json()["org_id"])
    pid = str((await alice.post(P, json={"title": "中文人名"})).json()["id"])

    # 一路跑到剧本门并通过它，下一步就是角色阶段。门的道数交给
    # `advance_to_gate`，这条用例要测的是中文 ref，不是门有几道。
    await advance_to_gate(alice, pid, "setup", user_input=SOURCE)
    await approve_gate(alice, pid, "setup")

    # 这一步以前是 500："服务暂时不可用"，项目从此卡死
    r = await alice.post(f"{P}/{pid}/advance", json={"user_input": ""})
    assert r.status_code == 200, r.text
    # 这一次 advance 默认跑到门口：角色 → 场景 → 停在空间锚点门（门③）
    assert r.json()["stage"] == "await_anchors"

    await db.commit()
    project = await project_service.get_project(db, org_id=org_id, project_id=uuid.UUID(pid))
    refs = [c["ref"] for c in project.current_state_json["characters"]["characters"]]
    assert len(refs) == 3
    assert all(REF_PATTERN.match(ref) for ref in refs), refs
    assert "guan_zhang" in refs, "合法 ref 不该被改写"
    assert len(set(refs)) == 3

    # 一致性引擎按 ref 认人，兜底出来的 ref 必须真能落库
    profiles = await consistency.list_characters(db, org_id=org_id, project_id=uuid.UUID(pid))
    assert {p.ref for p in profiles} == set(refs)
