"""项目模块的唯一对外入口。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.errors import AppError
from apps.api.modules.project import repository as repo
from apps.api.modules.project.models import Project

MAX_PAGE_SIZE = 100


async def create_project(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    title: str,
    route_type: str | None = None,
    budget_cap_credits: int | None = None,
) -> Project:
    row = await repo.create(
        db,
        org_id=org_id,
        owner_user_id=owner_user_id,
        title=title,
        route_type=route_type,
        budget_cap_credits=budget_cap_credits,
    )
    await db.commit()
    return row


async def get_project(db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID) -> Project:
    """取不到就 404。

    注意这里对"不存在"和"属于别的租户"返回同样的 404，
    不返回 403——403 等于确认了这个 ID 存在，是一个可枚举的信息泄露。
    """
    row = await repo.get(db, org_id=org_id, project_id=project_id)
    if row is None:
        raise AppError("common.not_found", message=f"project {project_id}")
    return row


async def list_projects(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    limit: int = 20,
    cursor: datetime | None = None,
    status: str | None = None,
) -> tuple[list[Project], datetime | None]:
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    # 多取一条用来判断还有没有下一页，避免额外的 count 查询
    rows = await repo.list_page(db, org_id=org_id, limit=limit + 1, cursor=cursor, status=status)
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = page[-1].created_at if has_more and page else None
    return page, next_cursor


async def update_project(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    title: str | None = None,
    route_type: str | None = None,
    budget_cap_credits: int | None = None,
) -> Project:
    row = await get_project(db, org_id=org_id, project_id=project_id)
    if title is not None:
        row.title = title.strip()
    if route_type is not None:
        row.route_type = route_type
    if budget_cap_credits is not None:
        row.budget_cap_credits = budget_cap_credits
    await db.commit()
    return row


async def delete_project(db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID) -> None:
    row = await get_project(db, org_id=org_id, project_id=project_id)
    await repo.soft_delete(db, row)
    await db.commit()


async def project_exists(db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID) -> bool:
    return await repo.get(db, org_id=org_id, project_id=project_id) is not None
