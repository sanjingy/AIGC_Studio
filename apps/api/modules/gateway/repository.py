"""Gateway 两张表的读写。每个查询都带 `org_id`。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.modules.gateway.models import OrgModelDefault, OrgProviderConnection

# ------------------------------------------------------------ 组织默认


async def get_default(
    db: AsyncSession, *, org_id: uuid.UUID, capability: str
) -> OrgModelDefault | None:
    return (
        await db.execute(
            select(OrgModelDefault).where(
                OrgModelDefault.org_id == org_id,
                OrgModelDefault.capability == capability,
                OrgModelDefault.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()


async def list_defaults(db: AsyncSession, *, org_id: uuid.UUID) -> list[OrgModelDefault]:
    return list(
        (
            await db.execute(
                select(OrgModelDefault).where(
                    OrgModelDefault.org_id == org_id,
                    OrgModelDefault.deleted_at.is_(None),
                )
            )
        ).scalars()
    )


async def upsert_default(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    capability: str,
    provider_id: str,
    model_id: str | None,
    key_source: str,
    updated_by: uuid.UUID,
) -> OrgModelDefault:
    row = await get_default(db, org_id=org_id, capability=capability)
    if row is None:
        row = OrgModelDefault(
            org_id=org_id,
            capability=capability,
            provider_id=provider_id,
            model_id=model_id,
            key_source=key_source,
            updated_by=updated_by,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row
    row.provider_id = provider_id
    row.model_id = model_id
    row.key_source = key_source
    row.updated_by = updated_by
    await db.flush()
    # `updated_at` 是服务端 onupdate，不 refresh 的话读它会触发同步 IO
    # （async 上下文里抛 MissingGreenlet）
    await db.refresh(row)
    return row


async def soft_delete_default(db: AsyncSession, *, row: OrgModelDefault) -> None:
    row.deleted_at = datetime.now(UTC)
    await db.flush()


# ------------------------------------------------------------ 供应商连接


async def get_connection(
    db: AsyncSession, *, org_id: uuid.UUID, connection_id: uuid.UUID
) -> OrgProviderConnection | None:
    """按 id 取本 org 的连接。别的 org 的同一个 id 查不到（调用方据此返 404）。"""
    return (
        await db.execute(
            select(OrgProviderConnection).where(
                OrgProviderConnection.id == connection_id,
                OrgProviderConnection.org_id == org_id,
                OrgProviderConnection.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()


async def list_connections(db: AsyncSession, *, org_id: uuid.UUID) -> list[OrgProviderConnection]:
    return list(
        (
            await db.execute(
                select(OrgProviderConnection)
                .where(
                    OrgProviderConnection.org_id == org_id,
                    OrgProviderConnection.deleted_at.is_(None),
                )
                .order_by(OrgProviderConnection.created_at, OrgProviderConnection.id)
            )
        ).scalars()
    )


async def count_connections(db: AsyncSession, *, org_id: uuid.UUID) -> int:
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(OrgProviderConnection)
                .where(
                    OrgProviderConnection.org_id == org_id,
                    OrgProviderConnection.deleted_at.is_(None),
                )
            )
        ).scalar_one()
    )


async def create_connection(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    label: str,
    preset_id: str | None,
    base_url: str,
    key_encrypted: str,
    models: list[dict[str, Any]],
    enabled: bool,
    created_by: uuid.UUID,
) -> OrgProviderConnection:
    row = OrgProviderConnection(
        org_id=org_id,
        label=label,
        preset_id=preset_id,
        base_url=base_url,
        key_encrypted=key_encrypted,
        models=models,
        enabled=enabled,
        created_by=created_by,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


async def update_connection(
    db: AsyncSession, *, row: OrgProviderConnection, changes: dict[str, Any]
) -> OrgProviderConnection:
    """就地改。`changes` 只含要改的列；JSONB 要整个换成新 list 才会被标脏。"""
    for column, value in changes.items():
        setattr(row, column, value)
    await db.flush()
    # `updated_at` 是服务端 onupdate，不 refresh 的话读它会触发同步 IO
    await db.refresh(row)
    return row


async def soft_delete_connection(db: AsyncSession, *, row: OrgProviderConnection) -> None:
    """软删。密文一并清空，理由同 `billing.repository.soft_delete_credential`。"""
    row.deleted_at = datetime.now(UTC)
    row.key_encrypted = ""
    row.secret_encrypted = None
    await db.flush()
