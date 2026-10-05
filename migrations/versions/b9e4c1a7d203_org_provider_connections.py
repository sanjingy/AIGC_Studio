"""org provider connections replace org_text_endpoints (ADR-039)

组织可添加多个自带 Key 的供应商连接（MULTI_PROVIDER_A1；ADR-039 第 1、4 条）。

—— 改的是什么 ——

1. **新表 `org_provider_connections`**：`label / preset_id / base_url / key_encrypted /
   secret_encrypted / models JSONB [{model_id, protocol}] / enabled / created_by`。
   一个 org 多行，**没有 capability 列**（能力由协议在代码白名单里推出）。
2. **`org_text_endpoints` 的每一行（含软删）按原 id 拷进新表**：
   `models = [{model_id: 原模型, protocol: "openai_chat"}]`、`preset_id = NULL`、`enabled = true`，
   时间戳与 `created_by` 原样保留。沿用原 id 是为了降级能逐行还原。
3. **引用改写**：`org_model_defaults.provider_id = 'provider.custom.text'` →
   `'provider.org:<该 org 未删端点的 id>'`（`model_id` 保持 NULL = 用连接里第一个文本模型，
   `key_source` 原本就只能是 `org`）；`projects.model_preference.text_generation` 同样改写。
   该 org 已经没有未删端点的引用保持原值——解析层把 `provider.custom.text` 当作
   "连接不存在"报可读错误，与迁移前"端点已删就报错"一致，不静默落到平台。
4. `org_model_defaults.provider_id` 放宽到 64 字符（`provider.org:` + UUID = 49）。
5. 删除 `org_text_endpoints`。

—— 降级 ——

**对升级迁过来的数据逐字还原**（同 id、同字段、同时间戳，引用改回 `provider.custom.text`）。
升级之后新增的数据有损：旧表一个 org 只能放一个端点、只能是文本，所以每个 org 只还原
一个连接——优先被文本组织默认指向的，其次最近更新的、启用的、含 `openai_chat` 模型的；
指向其他连接的组织默认硬删、项目偏好删键（旧结构表达不了）；出图连接全部丢弃。
降级用于回滚一次刚出问题的部署，不是来回切换。

Revision ID: b9e4c1a7d203
Revises: 3a9d2c7e5b10
Create Date: 2026-10-05 22:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b9e4c1a7d203"
down_revision: str | None = "3a9d2c7e5b10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_ID = "provider.custom.text"
ORG_PREFIX = "provider.org:"


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


def _create_text_endpoints() -> None:
    """与 `3a9d2c7e5b10` 建的形状逐字一致。"""
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


def _drop_text_endpoints() -> None:
    op.drop_index(
        "uq_org_text_endpoints_org",
        table_name="org_text_endpoints",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(op.f("ix_org_text_endpoints_org_id"), table_name="org_text_endpoints")
    op.drop_index(op.f("ix_org_text_endpoints_deleted_at"), table_name="org_text_endpoints")
    op.drop_table("org_text_endpoints")


def upgrade() -> None:
    op.create_table(
        "org_provider_connections",
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("preset_id", sa.String(length=64), nullable=True),
        sa.Column("base_url", sa.String(length=512), nullable=False),
        sa.Column("key_encrypted", sa.Text(), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "models",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        *_base_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_provider_connections")),
    )
    op.create_index(
        op.f("ix_org_provider_connections_deleted_at"),
        "org_provider_connections",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_org_provider_connections_org_id"),
        "org_provider_connections",
        ["org_id"],
        unique=False,
    )
    op.alter_column(
        "org_model_defaults",
        "provider_id",
        existing_type=sa.String(length=32),
        type_=sa.String(length=64),
        existing_nullable=False,
    )

    op.execute(
        """
        INSERT INTO org_provider_connections
            (id, org_id, label, preset_id, base_url, key_encrypted, secret_encrypted,
             models, enabled, created_by, created_at, updated_at, deleted_at)
        SELECT e.id, e.org_id, e.label, NULL, e.base_url, e.key_encrypted, NULL,
               jsonb_build_array(
                   jsonb_build_object('model_id', e.model_id, 'protocol', 'openai_chat')
               ),
               true, e.created_by, e.created_at, e.updated_at, e.deleted_at
        FROM org_text_endpoints e
        """
    )
    # 旧表的部分唯一索引保证每个 org 至多一个未删端点，下面两条 JOIN 不会一对多
    op.execute(
        f"""
        UPDATE org_model_defaults d
        SET provider_id = '{ORG_PREFIX}' || e.id::text
        FROM org_text_endpoints e
        WHERE d.provider_id = '{LEGACY_ID}'
          AND e.org_id = d.org_id AND e.deleted_at IS NULL
        """
    )
    op.execute(
        f"""
        UPDATE projects p
        SET model_preference = jsonb_set(
            p.model_preference, '{{text_generation}}', to_jsonb('{ORG_PREFIX}' || e.id::text)
        )
        FROM org_text_endpoints e
        WHERE p.model_preference ->> 'text_generation' = '{LEGACY_ID}'
          AND e.org_id = p.org_id AND e.deleted_at IS NULL
        """
    )
    _drop_text_endpoints()


def downgrade() -> None:
    _create_text_endpoints()

    # 每个 org 还原一个未删连接（见文件头），外加所有已软删、含文本模型的连接
    op.execute(
        f"""
        WITH chat AS (
            SELECT c.*,
                   (SELECT m ->> 'model_id'
                    FROM jsonb_array_elements(c.models) WITH ORDINALITY AS t(m, i)
                    WHERE m ->> 'protocol' = 'openai_chat'
                    ORDER BY i LIMIT 1) AS chat_model,
                   EXISTS (
                       SELECT 1 FROM org_model_defaults d
                       WHERE d.org_id = c.org_id AND d.deleted_at IS NULL
                         AND d.capability = 'text_generation'
                         AND d.provider_id = '{ORG_PREFIX}' || c.id::text
                   ) AS is_default
            FROM org_provider_connections c
        ),
        ranked AS (
            SELECT chat.*, row_number() OVER (
                PARTITION BY org_id
                ORDER BY is_default DESC, enabled DESC, updated_at DESC, id
            ) AS rn
            FROM chat
            WHERE deleted_at IS NULL AND chat_model IS NOT NULL
        )
        INSERT INTO org_text_endpoints
            (id, org_id, label, base_url, model_id, key_encrypted, created_by,
             created_at, updated_at, deleted_at)
        SELECT id, org_id, label, base_url, left(chat_model, 128), key_encrypted, created_by,
               created_at, updated_at, deleted_at
        FROM ranked WHERE rn = 1
        UNION ALL
        SELECT id, org_id, label, base_url, left(chat_model, 128), key_encrypted, created_by,
               created_at, updated_at, deleted_at
        FROM chat WHERE deleted_at IS NOT NULL AND chat_model IS NOT NULL
        """
    )

    # 引用：指向被还原连接的改回旧固定串；其余 provider.org 引用旧结构表达不了，删掉
    op.execute(
        f"""
        UPDATE org_model_defaults d
        SET provider_id = '{LEGACY_ID}', model_id = NULL
        FROM org_text_endpoints e
        WHERE d.capability = 'text_generation'
          AND d.provider_id = '{ORG_PREFIX}' || e.id::text
          AND e.org_id = d.org_id AND e.deleted_at IS NULL
        """
    )
    op.execute(f"DELETE FROM org_model_defaults WHERE provider_id LIKE '{ORG_PREFIX}%'")
    op.execute(
        f"""
        UPDATE projects p
        SET model_preference = jsonb_set(
            p.model_preference, '{{text_generation}}', to_jsonb('{LEGACY_ID}'::text)
        )
        FROM org_text_endpoints e
        WHERE e.org_id = p.org_id AND e.deleted_at IS NULL
          AND (p.model_preference ->> 'text_generation' = '{ORG_PREFIX}' || e.id::text
               OR p.model_preference ->> 'text_generation' LIKE '{ORG_PREFIX}' || e.id::text || ':%')
        """
    )
    op.execute(
        f"""
        UPDATE projects p
        SET model_preference = (
            SELECT COALESCE(jsonb_object_agg(kv.key, kv.value), '{{}}'::jsonb)
            FROM jsonb_each(p.model_preference) AS kv
            WHERE NOT (jsonb_typeof(kv.value) = 'string'
                       AND kv.value #>> '{{}}' LIKE '{ORG_PREFIX}%')
        )
        WHERE p.model_preference IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM jsonb_each_text(p.model_preference) AS t
              WHERE t.value LIKE '{ORG_PREFIX}%'
          )
        """
    )

    op.alter_column(
        "org_model_defaults",
        "provider_id",
        existing_type=sa.String(length=64),
        type_=sa.String(length=32),
        existing_nullable=False,
    )
    op.drop_index(op.f("ix_org_provider_connections_org_id"), table_name="org_provider_connections")
    op.drop_index(
        op.f("ix_org_provider_connections_deleted_at"), table_name="org_provider_connections"
    )
    op.drop_table("org_provider_connections")
