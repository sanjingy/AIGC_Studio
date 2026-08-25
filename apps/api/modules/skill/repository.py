"""org_skills 的取数。模块私有。

每个查询都带 `org_id`——Skill 是用户上传的东西，跨租户读到别人的
生产线定义等于把人家的工艺流程送出去。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.modules.skill.models import OrgSkill


async def list_for_org(db: AsyncSession, *, org_id: uuid.UUID, limit: int = 100) -> list[OrgSkill]:
    stmt = (
        select(OrgSkill)
        .where(OrgSkill.org_id == org_id, OrgSkill.deleted_at.is_(None))
        .order_by(OrgSkill.created_at.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def get(db: AsyncSession, *, org_id: uuid.UUID, skill_id: uuid.UUID) -> OrgSkill | None:
    stmt = select(OrgSkill).where(
        OrgSkill.id == skill_id,
        OrgSkill.org_id == org_id,
        OrgSkill.deleted_at.is_(None),
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def add(db: AsyncSession, row: OrgSkill) -> OrgSkill:
    db.add(row)
    return row
