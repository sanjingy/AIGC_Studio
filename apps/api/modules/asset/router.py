from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query, status

from apps.api.core.config import get_settings
from apps.api.core.errors import AppError
from apps.api.modules.asset import service
from apps.api.modules.asset.schemas import (
    AssetOut,
    AssetPage,
    DownloadUrlOut,
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
