"""资产 ORM。模块私有。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.core.models import OrgEntity

ASSET_TYPES = ("image", "video", "audio", "text", "document", "workflow", "lora")

# pending → ready 是正常路径；pending 超过 24h 由清理任务回收。
ASSET_STATUSES = ("pending", "ready", "failed")


class Asset(OrgEntity):
    __tablename__ = "assets"

    # 可空：用户可能先传小说原文再建项目
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)

    type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)

    # 声明值在建记录时写入，实测值在 complete 时回填，不一致则拒绝
    declared_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)

    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    # 合规预留（18_Compliance.md）：审核状态与水印标记
    moderation_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    watermarked: Mapped[bool] = mapped_column(nullable=False, default=False)

    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_assets_org_project_created", "org_id", "project_id", "created_at"),
        Index("ix_assets_org_checksum", "org_id", "checksum"),
        # 清理任务扫未完成上传用
        Index("ix_assets_status_created", "status", "created_at"),
    )
