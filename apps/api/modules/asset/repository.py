"""资产数据访问。模块私有。所有查询强制带 org_id。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.modules.asset.models import Asset


def _scoped(org_id: uuid.UUID) -> Select[tuple[Asset]]:
    return select(Asset).where(Asset.org_id == org_id, Asset.deleted_at.is_(None))


async def create(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    project_id: uuid.UUID | None,
    asset_type: str,
    filename: str,
    storage_key: str,
    mime_type: str,
    declared_size_bytes: int,
) -> Asset:
    row = Asset(
        org_id=org_id,
        owner_user_id=owner_user_id,
        project_id=project_id,
        type=asset_type,
        filename=filename,
        storage_key=storage_key,
        mime_type=mime_type,
        declared_size_bytes=declared_size_bytes,
    )
    db.add(row)
    await db.flush()
    return row


async def get(db: AsyncSession, *, org_id: uuid.UUID, asset_id: uuid.UUID) -> Asset | None:
    return (await db.execute(_scoped(org_id).where(Asset.id == asset_id))).scalar_one_or_none()


async def list_page(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID | None,
    asset_type: str | None,
    limit: int,
    cursor: datetime | None,
) -> list[Asset]:
    stmt = _scoped(org_id).where(Asset.status == "ready")
    if project_id is not None:
        stmt = stmt.where(Asset.project_id == project_id)
    if asset_type:
        stmt = stmt.where(Asset.type == asset_type)
    if cursor:
        stmt = stmt.where(Asset.created_at < cursor)
    stmt = stmt.order_by(Asset.created_at.desc()).limit(limit)
    return list((await db.execute(stmt)).scalars())


async def list_abandoned(db: AsyncSession, *, older_than: datetime, limit: int) -> list[Asset]:
    """扫出未完成的上传，供清理任务回收。

    这个查询**不带 org_id**——它是跨租户的运维任务，不是用户请求。
    唯一的例外，所以显式写在这里而不是混在上面。
    """
    stmt = (
        select(Asset)
        .where(
            Asset.status == "pending",
            Asset.created_at < older_than,
            Asset.deleted_at.is_(None),
        )
        .order_by(Asset.created_at)
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars())


async def mark_ready(db: AsyncSession, asset: Asset, *, size_bytes: int, checksum: str) -> None:
    asset.status = "ready"
    asset.size_bytes = size_bytes
    asset.checksum = checksum
    asset.completed_at = datetime.now(UTC)


async def soft_delete(db: AsyncSession, asset: Asset) -> None:
    asset.deleted_at = datetime.now(UTC)
