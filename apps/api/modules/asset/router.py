from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query, status

from apps.api.core.config import get_settings
from apps.api.core.errors import AppError
from apps.api.modules.asset import character, folders, library, service
from apps.api.modules.asset.models import CharacterEntry
from apps.api.modules.asset.schemas import (
    AssetOut,
    AssetPage,
    CharacterEntryOut,
    CharacterFromReferenceIn,
    CharacterGeneratedOut,
    DownloadUrlOut,
    FolderIn,
    FolderItemIn,
    FolderOut,
    LibraryOut,
    ProfileEntryOut,
    StorageUsageOut,
    UploadRequestIn,
    UploadTicketOut,
)
from apps.api.modules.auth.deps import CurrentUser, DbSession
from apps.api.modules.project import service as project_service

router = APIRouter(prefix="/assets", tags=["assets"])


def _decode_cursor(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise AppError("common.validation_failed", message="cursor 格式非法") from exc


@router.post("/upload-url", response_model=UploadTicketOut, status_code=status.HTTP_201_CREATED)
async def create_upload_url(
    payload: UploadRequestIn, user: CurrentUser, db: DbSession
) -> UploadTicketOut:
    # 指定了项目就必须验证它属于本租户，否则可以把资产挂到别人的项目上
    if payload.project_id is not None:
        await project_service.get_project(db, org_id=user.org_id, project_id=payload.project_id)

    ticket = await service.create_upload(
        db,
        org_id=user.org_id,
        owner_user_id=user.id,
        project_id=payload.project_id,
        filename=payload.filename,
        mime_type=payload.mime_type,
        size_bytes=payload.size_bytes,
    )
    return UploadTicketOut(
        asset=AssetOut.model_validate(ticket.asset),
        upload_url=ticket.upload_url,
        expires_at=ticket.expires_at,
    )


@router.post("/{asset_id}/complete", response_model=AssetOut)
async def complete_upload(asset_id: uuid.UUID, user: CurrentUser, db: DbSession) -> AssetOut:
    row = await service.complete_upload(db, org_id=user.org_id, asset_id=asset_id)
    return AssetOut.model_validate(row)


@router.get("", response_model=AssetPage)
async def list_assets(
    user: CurrentUser,
    db: DbSession,
    project_id: uuid.UUID | None = None,
    asset_type: str | None = Query(None, alias="type"),
    limit: int = Query(40, ge=1, le=100),
    cursor: str | None = None,
) -> AssetPage:
    rows, next_cursor = await service.list_assets(
        db,
        org_id=user.org_id,
        project_id=project_id,
        asset_type=asset_type,
        limit=limit,
        cursor=_decode_cursor(cursor),
    )
    return AssetPage(
        items=[AssetOut.model_validate(r) for r in rows],
        next_cursor=next_cursor.isoformat() if next_cursor else None,
    )


def _character_out(row: CharacterEntry, folder_id: uuid.UUID | None) -> CharacterEntryOut:
    return CharacterEntryOut(
        id=row.id,
        title=row.title,
        source_text=row.source_text,
        agent_id=row.agent_id,
        output=row.output_json,
        model_id=row.model_id,
        created_at=row.created_at,
        folder_id=folder_id,
    )


def _folder_out(
    folder_id: uuid.UUID, name: str, item_count: int, created_at: datetime
) -> FolderOut:
    return FolderOut(id=folder_id, name=name, item_count=item_count, created_at=created_at)


def _usage_out(usage: service.QuotaUsage) -> StorageUsageOut:
    return StorageUsageOut(
        used_bytes=usage.used_bytes,
        quota_bytes=usage.quota_bytes,
        free_bytes=usage.free_bytes,
        percent_used=usage.percent_used,
    )


# 注意声明顺序：这几条必须排在 `/{asset_id}` 前面。
# FastAPI 按声明顺序匹配，排在后面的话 "library" / "folders" 会先撞上
# UUID 路径参数，得到一个 422 而不是这个接口。
@router.get("/usage", response_model=StorageUsageOut)
async def get_usage(user: CurrentUser, db: DbSession) -> StorageUsageOut:
    """当前用户的资产库用量。配额跨项目按账号算，不是按项目算。"""
    usage = await service.quota_usage(db, org_id=user.org_id, owner_user_id=user.id)
    return _usage_out(usage)


@router.get("/library", response_model=LibraryOut)
async def get_library(
    user: CurrentUser,
    db: DbSession,
    asset_type: str | None = Query(None, alias="type"),
    limit: int = Query(40, ge=1, le=100),
    cursor: str | None = None,
    folder_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
) -> LibraryOut:
    """我的资产库：二进制资产 + 角色/场景档案 + 独立角色档案 + 文件夹 + 用量。

    只返回本人的东西。org 作用域仍然照带——同一个 org 里的别人的资产
    不属于"我的资产库"，跨 org 的更是连存在与否都不该透出。

    不带 folder_id 是"全部"视图（按项目分组展示由前端做）；
    带上就只列那个文件夹里的东西。

    不带 project_id 是"全部项目"，与加这个参数之前完全一致；带上就只列
    那个项目里的东西（项目内素材页用）。跨租户的 project_id 走
    `project_service.get_project` 落 404，与其它带项目 id 的入口一致。
    """
    result = await library.get_library(
        db,
        org_id=user.org_id,
        owner_user_id=user.id,
        asset_type=asset_type,
        limit=limit,
        cursor=_decode_cursor(cursor),
        folder_id=folder_id,
        project_id=project_id,
    )
    return LibraryOut(
        usage=_usage_out(result.usage),
        assets=[
            AssetOut.model_validate(a).model_copy(
                update={"folder_id": result.folder_of.get(("asset", a.id))}
            )
            for a in result.assets
        ],
        next_cursor=result.next_cursor.isoformat() if result.next_cursor else None,
        profiles=[
            ProfileEntryOut(
                project_id=p.project_id,
                project_title=p.project_title,
                kind=p.kind,
                agent_id=p.agent_id,
                run_id=p.run_id,
                output=p.output,
                created_at=p.created_at,
                folder_id=p.folder_id,
            )
            for p in result.profiles
        ],
        characters=[_character_out(c.entry, c.folder_id) for c in result.characters],
        folders=[_folder_out(f.id, f.name, f.item_count, f.created_at) for f in result.folders],
    )


# ---------------------------------------------------------------- 文件夹


@router.get("/folders", response_model=list[FolderOut])
async def list_folders(user: CurrentUser, db: DbSession) -> list[FolderOut]:
    # 只查文件夹，不跑整个资产库聚合——侧边栏刷新一次没必要把所有项目
    # 和档案再扫一遍。
    rows, _ = await library.folder_index(db, org_id=user.org_id, owner_user_id=user.id)
    return [_folder_out(f.id, f.name, f.item_count, f.created_at) for f in rows]


@router.post("/folders", response_model=FolderOut, status_code=status.HTTP_201_CREATED)
async def create_folder(payload: FolderIn, user: CurrentUser, db: DbSession) -> FolderOut:
    row = await folders.create_folder(
        db, org_id=user.org_id, owner_user_id=user.id, name=payload.name
    )
    return _folder_out(row.id, row.name, 0, row.created_at)


@router.patch("/folders/{folder_id}", response_model=FolderOut)
async def rename_folder(
    folder_id: uuid.UUID, payload: FolderIn, user: CurrentUser, db: DbSession
) -> FolderOut:
    row = await folders.rename_folder(
        db, org_id=user.org_id, owner_user_id=user.id, folder_id=folder_id, name=payload.name
    )
    # 改名不影响里面有什么。条目数由列表接口去数，这里不为它多跑一次聚合查询。
    return _folder_out(row.id, row.name, 0, row.created_at)


@router.delete("/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(folder_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    """删文件夹。里面的资产与档案一件都不删，只是回到未分类。"""
    await folders.delete_folder(db, org_id=user.org_id, owner_user_id=user.id, folder_id=folder_id)


@router.put("/folder-items", status_code=status.HTTP_204_NO_CONTENT)
async def classify_item(payload: FolderItemIn, user: CurrentUser, db: DbSession) -> None:
    """把一个资产/档案归到某个文件夹；folder_id 为空表示移出。"""
    await folders.classify(
        db,
        org_id=user.org_id,
        owner_user_id=user.id,
        item_type=payload.item_type,
        item_id=payload.item_id,
        folder_id=payload.folder_id,
    )


# ---------------------------------------------------------------- 独立角色档案


@router.post(
    "/characters", response_model=CharacterGeneratedOut, status_code=status.HTTP_201_CREATED
)
async def create_character_from_reference(
    payload: CharacterFromReferenceIn, user: CurrentUser, db: DbSession
) -> CharacterGeneratedOut:
    """一段参考描述 → 一份正式角色档案，直接进资产库，不挂任何项目。

    走的是编排链路上的同一个 Agent（`visual.character.v1`）与同一个
    output_schema，只是输入形态不同。这是一次真实的 LLM 调用，
    照常走 Credits 预扣/结算。
    """
    result = await character.generate_from_reference(
        db, org_id=user.org_id, owner_user_id=user.id, description=payload.description
    )
    if payload.folder_id is not None:
        await folders.classify(
            db,
            org_id=user.org_id,
            owner_user_id=user.id,
            item_type="character",
            item_id=result.entry.id,
            folder_id=payload.folder_id,
        )
    return CharacterGeneratedOut(
        entry=_character_out(result.entry, payload.folder_id),
        reserved_credits=result.reserved,
        cost_credits=result.cost,
    )


@router.get("/characters", response_model=list[CharacterEntryOut])
async def list_characters(
    user: CurrentUser, db: DbSession, limit: int = Query(100, ge=1, le=200)
) -> list[CharacterEntryOut]:
    rows = await character.list_entries(db, org_id=user.org_id, owner_user_id=user.id, limit=limit)
    return [_character_out(r, None) for r in rows]


@router.get("/characters/{entry_id}", response_model=CharacterEntryOut)
async def get_character(entry_id: uuid.UUID, user: CurrentUser, db: DbSession) -> CharacterEntryOut:
    row = await character.get_entry(
        db, org_id=user.org_id, owner_user_id=user.id, entry_id=entry_id
    )
    return _character_out(row, None)


@router.delete("/characters/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_character(entry_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    await character.delete_entry(db, org_id=user.org_id, owner_user_id=user.id, entry_id=entry_id)


@router.get("/{asset_id}", response_model=AssetOut)
async def get_asset(asset_id: uuid.UUID, user: CurrentUser, db: DbSession) -> AssetOut:
    row = await service.get_asset(db, org_id=user.org_id, asset_id=asset_id)
    return AssetOut.model_validate(row)


@router.get("/{asset_id}/download-url", response_model=DownloadUrlOut)
async def get_download_url(asset_id: uuid.UUID, user: CurrentUser, db: DbSession) -> DownloadUrlOut:
    url = await service.download_url(db, org_id=user.org_id, asset_id=asset_id)
    ttl = get_settings().s3_presign_ttl_seconds
    return DownloadUrlOut(url=url, expires_at=datetime.now(UTC) + timedelta(seconds=ttl))


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(asset_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    await service.delete_asset(db, org_id=user.org_id, asset_id=asset_id)
