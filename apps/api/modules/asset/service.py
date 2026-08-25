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
from apps.api.modules.billing import service as billing_service

log = get_logger(__name__)

MAX_PAGE_SIZE = 100

# 单用户资产库容量上限，走 pricing_rules（ADR-014）。
# 这里只写**键名**，数值在库里（迁移 a1f3c07b52d4 灌的种子 = 1 GiB）。
# 写成 Python 常量的话，调一次容量要发一次版；而且分发出去的代码里
# 冻着一个随时会变的运营数字，比没有这个数字更糟。
QUOTA_RULE_KEY = "user_storage_quota_bytes"


@dataclass(frozen=True, slots=True)
class QuotaUsage:
    used_bytes: int
    #: None = 库里没配这条规则，等于不限容量
    quota_bytes: int | None

    @property
    def percent_used(self) -> int:
        """整数百分比。前端画进度条用，不需要小数，也不引入浮点。"""
        if not self.quota_bytes:
            return 0
        return min(100, self.used_bytes * 100 // self.quota_bytes)

    @property
    def free_bytes(self) -> int | None:
        if self.quota_bytes is None:
            return None
        return max(0, self.quota_bytes - self.used_bytes)


@dataclass(frozen=True, slots=True)
class UploadTicket:
    asset: Asset
    upload_url: str
    expires_at: datetime


async def quota_usage(
    db: AsyncSession, *, org_id: uuid.UUID, owner_user_id: uuid.UUID
) -> QuotaUsage:
    """某个用户的资产库用量与配额。"""
    rules = await billing_service.rules(db)
    used = await repo.sum_owned_bytes(db, org_id=org_id, owner_user_id=owner_user_id)
    return QuotaUsage(used_bytes=used, quota_bytes=rules.get(QUOTA_RULE_KEY))


async def ensure_quota(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    additional_bytes: int,
) -> QuotaUsage:
    """占用配额前的闸门。放行则返回**当前**用量，不放行则抛错。

    调用点必须在"真正花钱/真正写对象"之前：签发直传 URL 之前、
    调用上游出图之前。放在写完之后检查等于每次超限都白付一次上游成本。
    """
    usage = await quota_usage(db, org_id=org_id, owner_user_id=owner_user_id)

    if usage.quota_bytes is None:
        # 规则没配就不限容量。这里选择放行而不是拦截：配额是限制，
        # 缺一条限制的配置不该让整条上传链路瘫痪。真正的兜底是
        # 迁移里的种子数据 + 断言它存在的测试。
        log.warning("asset.quota.unconfigured", rule_key=QUOTA_RULE_KEY)
        return usage

    if usage.used_bytes + additional_bytes > usage.quota_bytes:
        raise AppError(
            "asset.quota.exceeded",
            message=(
                f"quota exceeded: used={usage.used_bytes} "
                f"+ requested={additional_bytes} > quota={usage.quota_bytes}"
            ),
            detail={
                "used_bytes": usage.used_bytes,
                "quota_bytes": usage.quota_bytes,
                "requested_bytes": additional_bytes,
                "free_bytes": usage.free_bytes,
            },
        )
    return usage


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

    # 配额闸门必须在这里，不能挪到 complete。签发直传 URL 之后字节就
    # 在路上了，那时再说"存不下"，用户已经把一个大文件传完了。
    await ensure_quota(db, org_id=org_id, owner_user_id=owner_user_id, additional_bytes=size_bytes)

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


async def register_generated(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID | None,
    filename: str,
    storage_key: str,
    mime_type: str,
    data: bytes,
    owner_user_id: uuid.UUID | None = None,
    metadata: dict[str, object] | None = None,
) -> Asset:
    """登记一份**平台生成**的资产。

    与用户上传的区别：字节已经在服务端手里，不需要三段式直传，
    也不需要 HEAD 校验——我们就是写入方。

    对外单独开这个入口，是为了让 Worker 不必去碰 asset 的 repository。
    跨模块直接摸对方的数据层会被 ruff banned-api 拦下（ADR-009），
    那条规则是对的：绕过 service 层意味着绕过它将来会加的审核、
    配额、生命周期逻辑。
    """
    import hashlib

    # 生成侧的归属人。Worker 目前只拿得到 org_id（任务是租户级的），
    # 缺省沿用原有写法把 org_id 当归属人，行为不变；M2 把出图接进
    # 生产链路时由调用方补上真实的 user_id，配额就自动按人算。
    owner = owner_user_id or org_id

    # 上游已经收过钱了，但对象还没落桶——在这里拦下来至少不会让
    # 超配额的用户把存储撑爆。真正省钱的拦截在调用上游之前（见
    # worker/jobs/generation.py 的预检）。
    await ensure_quota(db, org_id=org_id, owner_user_id=owner, additional_bytes=len(data))

    await storage.put_bytes(key=storage_key, data=data, content_type=mime_type)

    row = await repo.create(
        db,
        org_id=org_id,
        owner_user_id=owner,
        project_id=project_id,
        asset_type=asset_type_for(mime_type) or "image",
        filename=filename,
        storage_key=storage_key,
        mime_type=mime_type,
        declared_size_bytes=len(data),
    )
    row.metadata_json = {**(metadata or {}), "source": "generated"}
    await repo.mark_ready(
        db, row, size_bytes=len(data), checksum=hashlib.sha256(data).hexdigest()[:64]
    )
    await db.commit()
    log.info("asset.generated_registered", asset_id=str(row.id), bytes=len(data))
    return row


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
    owner_user_id: uuid.UUID | None = None,
) -> tuple[list[Asset], datetime | None]:
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    rows = await repo.list_page(
        db,
        org_id=org_id,
        project_id=project_id,
        asset_type=asset_type,
        limit=limit + 1,
        cursor=cursor,
        owner_user_id=owner_user_id,
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
