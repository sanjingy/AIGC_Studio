"""org model defaults, text custom endpoints, per-provider BYOK keys

模型上游配置（CONFIG_STAGE_UPLOAD_0924 · A；05_MODEL_GATEWAY.md §5.2 / §6.2）。

—— 改的是什么 ——

1. **新表 `org_model_defaults`**：组织给每个能力选的默认上游 + 模型 + 计费来源。
   三层解析的中间一层（项目选择 > 组织默认 > 平台目录默认）。部分唯一索引
   `(org_id, capability) WHERE deleted_at IS NULL`：一个能力一行，软删的不占位。
2. **新表 `org_text_endpoints`**：文本能力的 OpenAI 兼容自定义端点，一个 org 一个。
   **没有 capability 列**（§5.2 第 2 条）。Key 只存 AES-GCM 密文。
3. **`provider_credentials` 的唯一约束从 `(org_id, capability)` 放宽到
   `(org_id, capability, provider_id)`**：同一能力允许每家各存一把 Key。

—— 存量数据怎么迁 ——

旧 BYOK 行的语义是"这个能力有自己的 Key 就用它、按折扣计费"。新模型里
计费来源是组织默认上的显式字段，所以**每一条未删的旧凭证行补一条组织默认**：
`provider_id` 取那一行的、`model_id` 留空（走那家的目录默认顺序）、
`key_source='org'`、`updated_by` 取存 Key 的人。这样迁移前后同一个租户的
调用与计费逐字不变，而模型页上能直接看到"这一项用的是你自己的 Key"。

即使不补这一条，解析层对"没有组织默认"的旧规则也是"有 Key 就用 Key"——
补它是为了让状态显式可见，不是靠它才兼容。已经存在组织默认的（重复升级）不覆盖。

`projects.model_preference` 不动：旧值是 capability → 目录里的模型 id，
解析时按模型反查所属 Provider，仍然有效。

—— 降级 ——

有损，与 `e4b7c9d21f38` 同类：降级用于回滚一次刚出问题的部署，不是来回切换。
同一能力下存了多家 Key 的租户，降级时只保留**最近更新的那一把**，其余硬删——
旧约束放不下它们。组织默认与自定义端点整表删除。

Revision ID: 3a9d2c7e5b10
Revises: a8c3e91d6402
Create Date: 2026-09-24 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3a9d2c7e5b10"
down_revision: str | None = "a8c3e91d6402"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _base_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("org_id", sa.Uuid(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "org_model_defaults",
        sa.Column("capability", sa.String(length=32), nullable=False),
        sa.Column("provider_id", sa.String(length=32), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=True),
        sa.Column("key_source", sa.String(length=16), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        *_base_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_model_defaults")),
    )
    op.create_index(
        op.f("ix_org_model_defaults_deleted_at"), "org_model_defaults", ["deleted_at"], unique=False
    )
    op.create_index(
        op.f("ix_org_model_defaults_org_id"), "org_model_defaults", ["org_id"], unique=False
    )
    op.create_index(
        "uq_org_model_defaults_org_capability",
        "org_model_defaults",
        ["org_id", "capability"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "org_text_endpoints",
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("key_encrypted", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        *_base_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_text_endpoints")),
    )
    op.create_index(
        op.f("ix_org_text_endpoints_deleted_at"), "org_text_endpoints", ["deleted_at"], unique=False
    )
    op.create_index(
        op.f("ix_org_text_endpoints_org_id"), "org_text_endpoints", ["org_id"], unique=False
    )
    op.create_index(
        "uq_org_text_endpoints_org",
        "org_text_endpoints",
        ["org_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.drop_constraint(
        "uq_provider_credentials_org_capability", "provider_credentials", type_="unique"
    )
    op.create_unique_constraint(
        "uq_provider_credentials_org_capability_provider",
        "provider_credentials",
        ["org_id", "capability", "provider_id"],
    )

    # 旧 BYOK 行 → 显式的组织默认。旧约束保证了每个 (org, capability) 至多一行，
    # 所以这里不会撞新表的部分唯一索引；NOT EXISTS 让重复升级是幂等的。
    op.execute(
        """
        INSERT INTO org_model_defaults (org_id, capability, provider_id, model_id, key_source, updated_by)
        SELECT pc.org_id, pc.capability, pc.provider_id, NULL, 'org', pc.created_by
        FROM provider_credentials pc
        WHERE pc.deleted_at IS NULL
          AND NOT EXISTS (
            SELECT 1 FROM org_model_defaults d
            WHERE d.org_id = pc.org_id AND d.capability = pc.capability AND d.deleted_at IS NULL
          )
        """
    )


def downgrade() -> None:
    # 同一 (org, capability) 下只留最近更新的一行，旧约束才建得回去（有损，见文件头）
    op.execute(
        """
        DELETE FROM provider_credentials pc
        USING (
            SELECT id, row_number() OVER (
                PARTITION BY org_id, capability ORDER BY updated_at DESC, id
            ) AS rn
            FROM provider_credentials
        ) ranked
        WHERE pc.id = ranked.id AND ranked.rn > 1
        """
    )
    op.drop_constraint(
        "uq_provider_credentials_org_capability_provider", "provider_credentials", type_="unique"
    )
    op.create_unique_constraint(
        "uq_provider_credentials_org_capability",
        "provider_credentials",
        ["org_id", "capability"],
    )

    op.drop_index(
        "uq_org_text_endpoints_org",
        table_name="org_text_endpoints",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(op.f("ix_org_text_endpoints_org_id"), table_name="org_text_endpoints")
    op.drop_index(op.f("ix_org_text_endpoints_deleted_at"), table_name="org_text_endpoints")
    op.drop_table("org_text_endpoints")

    op.drop_index(
        "uq_org_model_defaults_org_capability",
        table_name="org_model_defaults",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(op.f("ix_org_model_defaults_org_id"), table_name="org_model_defaults")
    op.drop_index(op.f("ix_org_model_defaults_deleted_at"), table_name="org_model_defaults")
    op.drop_table("org_model_defaults")
