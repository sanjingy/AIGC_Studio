"""项目数据访问。模块私有。

**每个函数都必须接收 org_id 并写进 WHERE。**
这不是风格问题——漏一个就是跨租户数据泄露。
新增查询函数时照抄这个签名模式。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.modules.project.models import Project


def _scoped(org_id: uuid.UUID) -> Select[tuple[Project]]:
    return select(Project).where(Project.org_id == org_id, Project.deleted_at.is_(None))


async def create(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    title: str,
    route_type: str | None,
    budget_cap_credits: int | None,
) -> Project:
    row = Project(
        org_id=org_id,
        owner_user_id=owner_user_id,
        title=title,
        route_type=route_type,
        budget_cap_credits=budget_cap_credits,
    )
    db.add(row)
    await db.flush()
    return row


async def get(db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID) -> Project | None:
    stmt = _scoped(org_id).where(Project.id == project_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_page(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    limit: int,
    cursor: datetime | None,
    status: str | None,
) -> list[Project]:
    stmt = _scoped(org_id)
    if status:
        stmt = stmt.where(Project.status == status)
    if cursor:
        stmt = stmt.where(Project.created_at < cursor)
    # 游标分页，不用 offset：大表 offset 慢，且插入会让翻页结果错位
    stmt = stmt.order_by(Project.created_at.desc()).limit(limit)
    return list((await db.execute(stmt)).scalars())


async def count(db: AsyncSession, *, org_id: uuid.UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(Project)
        .where(Project.org_id == org_id, Project.deleted_at.is_(None))
    )
    return (await db.execute(stmt)).scalar_one()


async def soft_delete(db: AsyncSession, project: Project) -> None:
    project.deleted_at = datetime.now(UTC)
