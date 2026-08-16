"""资产模块的唯一对外入口。

上传是三段式（10_API.md）：
    1. POST /assets/upload-url   建 pending 记录，签发直传 URL
    2. PUT  <upload_url>         客户端直传对象存储，不经过 API
    3. POST /assets/{id}/complete 服务端 HEAD 校验后置为 ready

第 3 步不能省。只有客户端说"我传完了"是不够的——它可能传了一半断线、
传了个不同大小的文件、或者根本没传。没校验就置 ready，
后面的生成任务会拿到一个坏文件，且排查时完全看不出问题在上传环节。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.config import get_settings
from apps.api.core.errors import AppError
from apps.api.core.logging import get_logger
from apps.api.modules.asset import repository as repo
from apps.api.modules.asset import storage
from apps.api.modules.asset.mime import asset_type_for, sanitize_filename
from apps.api.modules.asset.models import Asset

log = get_logger(__name__)

MAX_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class UploadTicket:
    asset: Asset
    upload_url: str
    expires_at: datetime


async def create_upload(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    project_id: uuid.UUID | None,
    filename: str,
    mime_type: str,
    size_bytes: int,
) -> UploadTicket:
    settings = get_settings()

    asset_type = asset_type_for(mime_type)
    if asset_type is None:
        raise AppError(
            "provider.params.invalid",
            message=f"unsupported mime {mime_type}",
            detail={"mime_type": mime_type},
        )

    if size_bytes <= 0 or size_bytes > settings.s3_max_upload_bytes:
        raise AppError(
            "asset.upload.too_large",
            message=f"size {size_bytes} exceeds {settings.s3_max_upload_bytes}",
            detail={"max_bytes": settings.s3_max_upload_bytes},
        )

    safe_name = sanitize_filename(filename)
    asset_id = uuid.uuid4()
    # 对象键带 org_id 前缀：即便签名逻辑出问题，也越不出本租户目录
    storage_key = f"{org_id}/{asset_id}/{safe_name}"

    row = await repo.create(
        db,
        org_id=org_id,
        owner_user_id=owner_user_id,
        project_id=project_id,
        asset_type=asset_type,
        filename=safe_name,
        storage_key=storage_key,
        mime_type=mime_type,
        declared_size_bytes=size_bytes,
    )
    row.id = asset_id
    await db.flush()

    url = await storage.presign_put(key=storage_key, content_type=mime_type)
    await db.commit()

    return UploadTicket(
        asset=row,
        upload_url=url,
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.s3_presign_ttl_seconds),
    )


async def complete_upload(db: AsyncSession, *, org_id: uuid.UUID, asset_id: uuid.UUID) -> Asset:
    asset = await _get_or_404(db, org_id=org_id, asset_id=asset_id)

    if asset.status == "ready":
        return asset  # 幂等：重复 complete 不报错

    info = await storage.head_object(key=asset.storage_key)
    if info is None:
        raise AppError("asset.upload.checksum_mismatch", message="object not found in storage")

    if info.size_bytes != asset.declared_size_bytes:
        # 大小对不上说明传了一半或传错了文件。删掉重来，
        # 不要留一个坏对象在桶里占空间。
        await storage.delete_object(key=asset.storage_key)
        asset.status = "failed"
        await db.commit()
        log.warning(
            "asset.size_mismatch",
            asset_id=str(asset.id),
            declared=asset.declared_size_bytes,
            actual=info.size_bytes,
        )
        raise AppError(
            "asset.upload.checksum_mismatch",
            message=f"size mismatch: declared={asset.declared_size_bytes} actual={info.size_bytes}",
        )

    await repo.mark_ready(db, asset, size_bytes=info.size_bytes, checksum=info.etag)
    await db.commit()
    return asset


async def get_asset(db: AsyncSession, *, org_id: uuid.UUID, asset_id: uuid.UUID) -> Asset:
    return await _get_or_404(db, org_id=org_id, asset_id=asset_id)


async def download_url(db: AsyncSession, *, org_id: uuid.UUID, asset_id: uuid.UUID) -> str:
    asset = await _get_or_404(db, org_id=org_id, asset_id=asset_id)
    if asset.status != "ready":
        raise AppError("common.not_found", message=f"asset {asset_id} not ready")
    return await storage.presign_get(key=asset.storage_key, download_name=asset.filename)


async def list_assets(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID | None = None,
    asset_type: str | None = None,
    limit: int = 40,
    cursor: datetime | None = None,
) -> tuple[list[Asset], datetime | None]:
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    rows = await repo.list_page(
        db,
        org_id=org_id,
        project_id=project_id,
        asset_type=asset_type,
        limit=limit + 1,
        cursor=cursor,
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    return page, (page[-1].created_at if has_more and page else None)


async def delete_asset(db: AsyncSession, *, org_id: uuid.UUID, asset_id: uuid.UUID) -> None:
    asset = await _get_or_404(db, org_id=org_id, asset_id=asset_id)
    # 只软删记录，不删对象：资产可能已被某个成片引用，
    # 真正的物理清理交给带保留期的清理任务。
    await repo.soft_delete(db, asset)
    await db.commit()


async def purge_abandoned_uploads(db: AsyncSession, *, limit: int = 200) -> int:
    """回收超时未完成的上传。由 Worker 定时调用。"""
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.upload_abandon_after_seconds)
    rows = await repo.list_abandoned(db, older_than=cutoff, limit=limit)

    for asset in rows:
        await storage.delete_object(key=asset.storage_key)
        await repo.soft_delete(db, asset)
    await db.commit()

    if rows:
        log.info("asset.purged_abandoned", count=len(rows))
    return len(rows)


async def _get_or_404(db: AsyncSession, *, org_id: uuid.UUID, asset_id: uuid.UUID) -> Asset:
    row = await repo.get(db, org_id=org_id, asset_id=asset_id)
    if row is None:
        # 跨租户访问同样返回 404，不返回 403——403 等于确认这个 ID 存在
        raise AppError("common.not_found", message=f"asset {asset_id}")
    return row
