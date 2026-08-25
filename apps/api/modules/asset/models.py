"""资产 ORM。模块私有。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
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
        # "我的资产库"与配额汇总都按 (租户, 用户) 查。配额检查在每次上传前
        # 都要跑一遍，缺索引时它会随资产表增长变成全表扫描。
        Index("ix_assets_org_owner_created", "org_id", "owner_user_id", "created_at"),
        Index("ix_assets_org_checksum", "org_id", "checksum"),
        # 清理任务扫未完成上传用
        Index("ix_assets_status_created", "status", "created_at"),
    )


# ---------------------------------------------------------------- 资产库分类

#: 能被归入文件夹的东西。三类的 id 分别指向三张表，故意不做外键——
#: profile 指的是 agent_runs.id，那是别的模块的表，跨模块加外键等于把
#: 两个模块的迁移绑死（ADR-009 的模块边界同样适用于数据库约束）。
FOLDER_ITEM_TYPES = ("asset", "profile", "character")


class AssetFolder(OrgEntity):
    """用户自建的资产库文件夹。

    **属于用户而不是项目。** 资产库本身就是跨项目的（配额按账号算），
    分类若挂到项目上，用户建的"配角参考"文件夹会在每个项目里各有一份。
    """

    __tablename__ = "asset_folders"

    owner_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)

    __table_args__ = (
        # 软删除的行不参与重名判断：删掉"参考图"再建一个同名的必须能成功。
        # 普通 UniqueConstraint 做不到这点（它看不见 deleted_at）。
        Index(
            "uq_asset_folders_owner_name",
            "org_id",
            "owner_user_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_asset_folders_org_owner_created", "org_id", "owner_user_id", "created_at"),
    )


class FolderItem(OrgEntity):
    """归类关系：某个东西在某个文件夹里。

    单独一张关联表，而不是给 assets 加一列 folder_id——因为要归类的四种
    东西里，角色/场景档案存在 `agent_runs.output_json`，那是 agent 模块的表，
    asset 模块不能往上面加列。一张关联表让四种东西走同一套归类逻辑，
    也不需要动任何现有表的结构。

    **一个东西同时只在一个文件夹里。** 多重归类的收益远小于它带来的
    "这份档案到底在哪"的困惑；移动就是改 folder_id，取消归类就是删这行。
    """

    __tablename__ = "asset_folder_items"

    folder_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    item_type: Mapped[str] = mapped_column(String(16), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)

    __table_args__ = (
        # 归类关系是硬删的（它没有审计价值），所以这里可以用普通唯一约束。
        UniqueConstraint("org_id", "item_type", "item_id", name="uq_folder_items_item"),
        Index("ix_folder_items_folder", "org_id", "folder_id"),
        Index("ix_folder_items_owner", "org_id", "owner_user_id"),
    )


class CharacterEntry(OrgEntity):
    """独立角色档案：一段参考描述直接生成的角色，**不挂任何项目**。

    为什么另起一张表而不是复用 `agent_runs`：那张表的 `project_id` 是
    NOT NULL，且资产库、项目详情、编排器全都按项目查它。把那一列改成可空，
    等于让所有"按项目查档案"的代码都要重新考虑 NULL 的语义——为一个新入口
    去动一条被四处依赖的约束，不划算也不安全。

    产出的 schema 与 `visual.character.v1` 完全一致（CharacterSheets），
    所以前端能用同一个组件渲染，将来要导入项目也不需要转换格式。
    """

    __tablename__ = "character_entries"

    owner_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)

    #: 列表展示用的标题，取产出里第一个角色名。空产出不可能——schema 要求至少一个。
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    #: 用户写的那段参考描述。要能回看"当初是照着什么生成的"。
    source_text: Mapped[str] = mapped_column(Text, nullable=False)

    agent_id: Mapped[str] = mapped_column(String(80), nullable=False)
    output_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    #: 提示词全文。与 agent_steps.resolved_prompt 同一个理由
    #: （17_ConsistencyEngine.md §7）：没有它，"为什么这次产出崩了"查不清。
    resolved_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)

    model_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: 实际结算掉的 Credits，最小单位整数
    cost: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    __table_args__ = (
        Index("ix_character_entries_org_owner_created", "org_id", "owner_user_id", "created_at"),
    )
