"""ORM 基座。

实现 09_Database.md 第 0 节的全局约定：
- 全表 UUID 主键
- 全表 org_id（多租户预留，现在加零成本，以后加是地狱级迁移）
- 全表 created_at / updated_at / deleted_at 软删除
- 金额一律 BIGINT 最小单位，禁止浮点
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, MetaData, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

# 约束命名规范。不定这个，Alembic 自动生成的迁移里约束名是随机的，
# 后续想 DROP CONSTRAINT 会很痛苦。
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )


class OrgScopedMixin:
    """多租户作用域。

    个人用户也有自己的 org，不做特例——特例会在做团队版时全面爆炸。
    """

    @declared_attr
    @classmethod
    def org_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(Uuid, nullable=False, index=True)


class BaseEntity(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """普通实体：有 id、时间戳、软删除，但不属于某个租户（如全局配置）。"""

    __abstract__ = True


class OrgEntity(BaseEntity, OrgScopedMixin):
    """租户作用域实体。绝大多数业务表继承它。"""

    __abstract__ = True


# 金额列的统一写法。业务代码里直接用它，避免有人写成 Float。
def money_column(*, nullable: bool = False, default: int | None = None) -> Mapped[int]:
    """金额列：BIGINT，单位为最小单位（1 Credit 的 1/1，即 ¥0.01）。

    绝不使用 Float/Numeric——浮点数存钱是财务事故的经典来源。
    """
    return mapped_column(BigInteger, nullable=nullable, default=default)
