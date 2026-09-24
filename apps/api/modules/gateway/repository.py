"""Gateway 两张表的读写。每个查询都带 `org_id`。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.modules.gateway.models import OrgModelDefault, OrgTextEndpoint

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


# ------------------------------------------------------------ 自定义端点


async def get_endpoint(db: AsyncSession, *, org_id: uuid.UUID) -> OrgTextEndpoint | None:
    return (
        await db.execute(
            select(OrgTextEndpoint).where(
                OrgTextEndpoint.org_id == org_id,
                OrgTextEndpoint.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()


async def save_endpoint(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    label: str,
    base_url: str,
    model_id: str,
    key_encrypted: str | None,
    created_by: uuid.UUID,
) -> OrgTextEndpoint:
    """新建或就地更新。`key_encrypted=None` 表示"Key 不变"，只改地址或模型。"""
    row = await get_endpoint(db, org_id=org_id)
    if row is None:
        if key_encrypted is None:
            raise ValueError("new endpoint requires a key")
        row = OrgTextEndpoint(
            org_id=org_id,
            label=label,
            base_url=base_url,
            model_id=model_id,
            key_encrypted=key_encrypted,
            created_by=created_by,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row
    row.label = label
    row.base_url = base_url
    row.model_id = model_id
    if key_encrypted is not None:
        row.key_encrypted = key_encrypted
    row.created_by = created_by
    await db.flush()
    await db.refresh(row)
    return row


async def soft_delete_endpoint(db: AsyncSession, *, row: OrgTextEndpoint) -> None:
    """软删。密文一并清空，理由同 `billing.repository.soft_delete_credential`。"""
    row.deleted_at = datetime.now(UTC)
    row.key_encrypted = ""
    await db.flush()
