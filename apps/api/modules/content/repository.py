"""变更记录的数据访问。模块私有。

**每个函数都必须接收 org_id 并写进 WHERE。**
这不是风格问题——漏一个就是跨租户数据泄露。变更记录里存的是
角色姓名、台词、场景描述这些项目正文，泄露的严重程度和产出本身一样。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.modules.content.models import ContentRevision


def _scoped(org_id: uuid.UUID, project_id: uuid.UUID) -> Select[tuple[ContentRevision]]:
    return select(ContentRevision).where(
        ContentRevision.org_id == org_id,
        ContentRevision.project_id == project_id,
        ContentRevision.deleted_at.is_(None),
    )


def stage(
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    batch_id: uuid.UUID,
    role: str,
    field_path: str,
    old_value: Any,
    new_value: Any,
    reason: str | None,
    actor_user_id: uuid.UUID | None,
    source: str,
    undone_batch_id: uuid.UUID | None = None,
) -> ContentRevision:
    """造一行，**不 add、不 flush**。

    整批攒好再一次性 `add_all`：这条写路径的全部价值就是"要么整批生效、
    要么一条都不落"，逐行入库会让一次中途失败留下半批记录，
    而半批记录撤销回去就是把产出改成一个谁也没要过的中间态。
    """
    return ContentRevision(
        org_id=org_id,
        project_id=project_id,
        batch_id=batch_id,
        role=role,
        field_path=field_path,
        old_value=old_value,
        new_value=new_value,
        reason=reason,
        actor_user_id=actor_user_id,
        source=source,
        undone_batch_id=undone_batch_id,
    )


async def add_all(db: AsyncSession, rows: list[ContentRevision]) -> None:
    db.add_all(rows)
    await db.flush()


async def batch_heads(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    limit: int,
    cursor: datetime | None,
    role: str | None,
) -> list[tuple[uuid.UUID, datetime]]:
    """先取"有哪些批"，再按批取行。

    不能直接对行分页：一批有几行是不定的，按行截断会把一批劈成两页，
    前端拿到半批就会显示一个"改了 2 处"而实际改了 5 处的记录。
    同一批的 `created_at` 一定相同——它是 `func.now()`，在 PostgreSQL 里
    取的是**事务开始时间**，一个事务内的所有行拿到同一个值。
    """
    stmt = (
        select(ContentRevision.batch_id, func.min(ContentRevision.created_at).label("at"))
        .where(
            ContentRevision.org_id == org_id,
            ContentRevision.project_id == project_id,
            ContentRevision.deleted_at.is_(None),
        )
        .group_by(ContentRevision.batch_id)
    )
    if role:
        # 一批只会有一个 role（一次 PATCH 只改一个阶段的产出，撤销继承它），
        # 所以按行过滤就够，不需要 HAVING。
        stmt = stmt.where(ContentRevision.role == role)
    if cursor:
        stmt = stmt.having(func.min(ContentRevision.created_at) < cursor)
    stmt = stmt.order_by(func.min(ContentRevision.created_at).desc()).limit(limit)
    return [(row[0], row[1]) for row in (await db.execute(stmt)).all()]


async def rows_of_batches(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    batch_ids: list[uuid.UUID],
) -> list[ContentRevision]:
    if not batch_ids:
        return []
    stmt = (
        _scoped(org_id, project_id)
        .where(ContentRevision.batch_id.in_(batch_ids))
        # id 参与排序：同一批同一时刻的多行需要一个稳定顺序，
        # 否则两次请求返回的字段顺序可能不同，前端 diff 会闪。
        .order_by(ContentRevision.created_at.desc(), ContentRevision.id)
    )
    return list((await db.execute(stmt)).scalars())


async def batch_rows(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, batch_id: uuid.UUID
) -> list[ContentRevision]:
    """一批的全部行，**按写入顺序**（撤销要按相反顺序回放）。"""
    stmt = (
        _scoped(org_id, project_id)
        .where(ContentRevision.batch_id == batch_id)
        .order_by(ContentRevision.created_at, ContentRevision.id)
    )
    return list((await db.execute(stmt)).scalars())


async def undo_batches_of(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, batch_ids: list[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    """这些批分别被哪一批撤销过。没被撤销的不出现在返回值里。"""
    if not batch_ids:
        return {}
    stmt = (
        select(ContentRevision.undone_batch_id, ContentRevision.batch_id)
        .where(
            ContentRevision.org_id == org_id,
            ContentRevision.project_id == project_id,
            ContentRevision.deleted_at.is_(None),
            ContentRevision.undone_batch_id.in_(batch_ids),
        )
        .order_by(ContentRevision.created_at)
    )
    out: dict[uuid.UUID, uuid.UUID] = {}
    for undone, by in (await db.execute(stmt)).all():
        if undone is not None:
            out.setdefault(undone, by)
    return out
