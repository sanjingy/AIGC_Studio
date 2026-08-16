"""账号体系的 ORM 模型。模块私有，跨模块请走 service 层。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.core.models import BaseEntity, OrgEntity


class Organization(BaseEntity):
    """租户。

    个人用户注册时也会自动创建一个 personal org——不做特例。
    做团队版时如果个人用户没有 org，整套权限模型都要打补丁。
    """

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="personal")
    # 与 users.org_id 互为外键，先有蛋后有鸡，所以这里可空：
    # 建 org → 建 user → 回填 owner。
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)


class User(OrgEntity):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(254), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="member")

    # 合规预留（18_Compliance.md）：实名认证
    realname_status: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    realname_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # 邮箱唯一，但软删除后要允许同邮箱重新注册，所以是部分唯一索引。
        # 普通 UNIQUE 会让注销过的邮箱永久不可用。
        Index(
            "uq_users_email_active",
            "email",
            unique=True,
            postgresql_where=mapped_column("deleted_at").is_(None),
        ),
    )


class RefreshToken(BaseEntity):
    """刷新令牌。

    只存哈希，不存明文——库被拖走也不能直接拿来登录。

    `family_id` 支撑重放检测：令牌轮换后旧令牌若再次出现，
    说明它被复制过，整个家族立即失效。
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)


class UserPreference(BaseEntity):
    __tablename__ = "user_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="zh-CN")
    theme: Mapped[str] = mapped_column(String(16), nullable=False, default="system")
    # 审核模式：strict / standard / auto（01_ProductSpec.md §5）
    production_policy: Mapped[str] = mapped_column(String(16), nullable=False, default="standard")
