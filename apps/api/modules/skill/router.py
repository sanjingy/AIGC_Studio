"""Skill 上传接口（ADR-026）。

只有上传、列出、看原文、删除。**没有"运行这个 Skill"**——运行时没接线，
放一个跑不起来的接口比不放更糟。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from apps.api.modules.auth.deps import CurrentUser, DbSession
from apps.api.modules.skill import service
from apps.api.modules.skill.schemas import SkillList, SkillOut, SkillSpecOut, SkillUploadIn

router = APIRouter(prefix="/skills", tags=["skills"])


def _out(view: service.SkillView) -> SkillOut:
    return SkillOut(
        id=view.id,
        name=view.name,
        version=view.version,
        status=view.status,
        validation_errors=view.validation_errors,
        created_at=view.created_at,
        skill_id=view.skill_id,
        route=view.route,
        stage_count=view.stage_count,
        gates=view.gates,
        missing_agents=view.missing_agents,
    )


@router.get("", response_model=SkillList)
async def list_skills(user: CurrentUser, db: DbSession) -> SkillList:
    """本 org 上传过的 Skill。别人 org 传的看不到。"""
    views = await service.list_for_org(db, org_id=user.org_id)
    return SkillList(items=[_out(v) for v in views])


@router.post("", response_model=SkillOut, status_code=status.HTTP_201_CREATED)
async def upload_skill(payload: SkillUploadIn, user: CurrentUser, db: DbSession) -> SkillOut:
    """上传一份 Skill YAML。

    **校验不通过也返回 201**：记录建成功了，只是 `status=invalid`。
    这不是接口失败——用户需要在列表里看见这一条并读到错误原文。
    真正的失败只有一种：内容根本不是 YAML（400）。
    """
    view = await service.upload(
        db,
        org_id=user.org_id,
        user_id=user.id,
        raw_yaml=payload.spec_yaml,
    )
    return _out(view)


@router.get("/{skill_id}/spec", response_model=SkillSpecOut)
async def read_spec(skill_id: uuid.UUID, user: CurrentUser, db: DbSession) -> SkillSpecOut:
    """取回上传的原文。跨租户返 404，不返 403。"""
    raw = await service.get_yaml(db, org_id=user.org_id, skill_id=skill_id)
    return SkillSpecOut(id=skill_id, spec_yaml=raw)


@router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(skill_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    await service.delete(db, org_id=user.org_id, skill_id=skill_id)
