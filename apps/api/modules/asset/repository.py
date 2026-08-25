"""资产数据访问。模块私有。所有查询强制带 org_id。"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.modules.asset.models import Asset, AssetFolder, CharacterEntry, FolderItem


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
    owner_user_id: uuid.UUID | None = None,
) -> list[Asset]:
    stmt = _scoped(org_id).where(Asset.status == "ready")
    if project_id is not None:
        stmt = stmt.where(Asset.project_id == project_id)
    if owner_user_id is not None:
        stmt = stmt.where(Asset.owner_user_id == owner_user_id)
    if asset_type:
        stmt = stmt.where(Asset.type == asset_type)
    if cursor:
        stmt = stmt.where(Asset.created_at < cursor)
    stmt = stmt.order_by(Asset.created_at.desc()).limit(limit)
    return list((await db.execute(stmt)).scalars())


async def sum_owned_bytes(db: AsyncSession, *, org_id: uuid.UUID, owner_user_id: uuid.UUID) -> int:
    """某个用户已占用的字节数，跨项目汇总。

    两个判断都是刻意的：

    **按 owner_user_id 而不是 project_id 汇总。** 配额是账号级的，
    按项目算等于"多建几个项目就能无限存"。

    **pending 也算，且按 declared_size_bytes 算。** pending 意味着预签名
    URL 已经签出去了，那些字节随时会落到桶里——不计入的话，用户可以一次
    连签 100 张票把配额撑爆，等 complete 时人已经付过上游的钱了。
    这就是"预扣"，和计费的 reserve 是同一个道理；没传成的那些由
    `purge_abandoned_uploads` 定时回收，占用会自己退回来。
    failed 不算：它的对象已经在 complete 里删掉了。
    """
    stmt = select(
        func.coalesce(
            func.sum(func.coalesce(Asset.size_bytes, Asset.declared_size_bytes)),
            0,
        )
    ).where(
        Asset.org_id == org_id,
        Asset.owner_user_id == owner_user_id,
        Asset.deleted_at.is_(None),
        Asset.status.in_(("pending", "ready")),
    )
    return int((await db.execute(stmt)).scalar_one())


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


# ---------------------------------------------------------------- 文件夹

#: 单个用户的文件夹数量上限。够任何真实用法，也挡住"脚本建十万个"。
MAX_FOLDERS = 200
#: 一次取多少条归类关系。够覆盖一个用户的全部资产，再多说明该做服务端分页了。
MAX_FOLDER_ITEMS = 2000


async def create_folder(
    db: AsyncSession, *, org_id: uuid.UUID, owner_user_id: uuid.UUID, name: str
) -> AssetFolder:
    row = AssetFolder(org_id=org_id, owner_user_id=owner_user_id, name=name)
    db.add(row)
    await db.flush()
    return row


async def get_folder(
    db: AsyncSession, *, org_id: uuid.UUID, folder_id: uuid.UUID
) -> AssetFolder | None:
    stmt = select(AssetFolder).where(
        AssetFolder.org_id == org_id,
        AssetFolder.id == folder_id,
        AssetFolder.deleted_at.is_(None),
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_folders(
    db: AsyncSession, *, org_id: uuid.UUID, owner_user_id: uuid.UUID
) -> list[AssetFolder]:
    stmt = (
        select(AssetFolder)
        .where(
            AssetFolder.org_id == org_id,
            AssetFolder.owner_user_id == owner_user_id,
            AssetFolder.deleted_at.is_(None),
        )
        .order_by(AssetFolder.created_at)
        .limit(MAX_FOLDERS)
    )
    return list((await db.execute(stmt)).scalars())


async def count_folders(db: AsyncSession, *, org_id: uuid.UUID, owner_user_id: uuid.UUID) -> int:
    stmt = select(func.count()).where(
        AssetFolder.org_id == org_id,
        AssetFolder.owner_user_id == owner_user_id,
        AssetFolder.deleted_at.is_(None),
    )
    return int((await db.execute(stmt)).scalar_one())


async def soft_delete_folder(db: AsyncSession, folder: AssetFolder) -> None:
    folder.deleted_at = datetime.now(UTC)


# ---------------------------------------------------------------- 归类关系


async def list_folder_items(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    folder_id: uuid.UUID | None = None,
) -> list[FolderItem]:
    stmt = select(FolderItem).where(
        FolderItem.org_id == org_id, FolderItem.owner_user_id == owner_user_id
    )
    if folder_id is not None:
        stmt = stmt.where(FolderItem.folder_id == folder_id)
    stmt = stmt.order_by(FolderItem.created_at.desc()).limit(MAX_FOLDER_ITEMS)
    return list((await db.execute(stmt)).scalars())


async def get_folder_item(
    db: AsyncSession, *, org_id: uuid.UUID, item_type: str, item_id: uuid.UUID
) -> FolderItem | None:
    stmt = select(FolderItem).where(
        FolderItem.org_id == org_id,
        FolderItem.item_type == item_type,
        FolderItem.item_id == item_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def put_folder_item(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    folder_id: uuid.UUID,
    item_type: str,
    item_id: uuid.UUID,
) -> FolderItem:
    """把一个东西放进文件夹。已归过类就原地改，不新增一行。

    原地改而不是"删旧的再插新的"：后者会撞 uq_folder_items_item，
    而且中间那一刻这个资产不属于任何文件夹。
    """
    existing = await get_folder_item(db, org_id=org_id, item_type=item_type, item_id=item_id)
    if existing is not None:
        existing.folder_id = folder_id
        await db.flush()
        return existing

    row = FolderItem(
        org_id=org_id,
        owner_user_id=owner_user_id,
        folder_id=folder_id,
        item_type=item_type,
        item_id=item_id,
    )
    db.add(row)
    await db.flush()
    return row


async def delete_folder_item(
    db: AsyncSession, *, org_id: uuid.UUID, item_type: str, item_id: uuid.UUID
) -> None:
    """取消归类。硬删——归类关系没有审计价值，留着只会挡住唯一约束。"""
    await db.execute(
        delete(FolderItem).where(
            FolderItem.org_id == org_id,
            FolderItem.item_type == item_type,
            FolderItem.item_id == item_id,
        )
    )


async def delete_items_of_folder(
    db: AsyncSession, *, org_id: uuid.UUID, folder_id: uuid.UUID
) -> None:
    """删文件夹时把里面的归类关系一并清掉。

    **只删关系，不删东西。** 用户删的是一个分类，不是分类里的资产——
    连带删掉真实文件是不可逆的，而这个操作在 UI 上只是个"删除文件夹"。
    """
    await db.execute(
        delete(FolderItem).where(FolderItem.org_id == org_id, FolderItem.folder_id == folder_id)
    )


# ---------------------------------------------------------------- 独立角色档案

MAX_CHARACTER_ENTRIES = 200


async def create_character_entry(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    entry_id: uuid.UUID,
    title: str,
    source_text: str,
    agent_id: str,
    output_json: dict[str, Any],
    resolved_prompt: str | None,
    model_id: str | None,
    tokens_in: int,
    tokens_out: int,
    cost: int,
) -> CharacterEntry:
    row = CharacterEntry(
        id=entry_id,
        org_id=org_id,
        owner_user_id=owner_user_id,
        title=title,
        source_text=source_text,
        agent_id=agent_id,
        output_json=output_json,
        resolved_prompt=resolved_prompt,
        model_id=model_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost=cost,
    )
    db.add(row)
    await db.flush()
    return row


async def get_character_entry(
    db: AsyncSession, *, org_id: uuid.UUID, entry_id: uuid.UUID
) -> CharacterEntry | None:
    stmt = select(CharacterEntry).where(
        CharacterEntry.org_id == org_id,
        CharacterEntry.id == entry_id,
        CharacterEntry.deleted_at.is_(None),
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_character_entries(
    db: AsyncSession, *, org_id: uuid.UUID, owner_user_id: uuid.UUID, limit: int
) -> list[CharacterEntry]:
    stmt = (
        select(CharacterEntry)
        .where(
            CharacterEntry.org_id == org_id,
            CharacterEntry.owner_user_id == owner_user_id,
            CharacterEntry.deleted_at.is_(None),
        )
        .order_by(CharacterEntry.created_at.desc())
        .limit(min(limit, MAX_CHARACTER_ENTRIES))
    )
    return list((await db.execute(stmt)).scalars())


async def soft_delete_character_entry(db: AsyncSession, entry: CharacterEntry) -> None:
    entry.deleted_at = datetime.now(UTC)


async def list_assets_by_ids(
    db: AsyncSession, *, org_id: uuid.UUID, asset_ids: Sequence[uuid.UUID]
) -> list[Asset]:
    """按 id 批量取资产。文件夹视图用——那里的顺序由归类关系决定，不分页。"""
    if not asset_ids:
        return []
    stmt = (
        _scoped(org_id)
        .where(Asset.status == "ready", Asset.id.in_(list(asset_ids)))
        .order_by(Asset.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars())


async def list_character_entries_by_ids(
    db: AsyncSession, *, org_id: uuid.UUID, entry_ids: Sequence[uuid.UUID]
) -> list[CharacterEntry]:
    if not entry_ids:
        return []
    stmt = (
        select(CharacterEntry)
        .where(
            CharacterEntry.org_id == org_id,
            CharacterEntry.deleted_at.is_(None),
            CharacterEntry.id.in_(list(entry_ids)),
        )
        .order_by(CharacterEntry.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars())
