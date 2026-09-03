"""content revisions

字段级变更记录（ADR-029）。一行 = 一个字段的一次改动，
同一次 UI 操作的若干行共享 `batch_id`，撤销以批为单位。

`old_value` / `new_value` 是 **NOT NULL 的 JSONB**，不是可空列：
JSON 的 null 与"这一列没填"是两件事，混成 SQL NULL 会让撤销把一个
本来存在、值为 null 的字段直接写没。SQLAlchemy 的 JSON 类型默认
`none_as_null=False`，Python 的 None 落成 JSON 的 null，正是要的语义。

不加任何外键：`project_id` / `actor_user_id` 指向别的模块的表，
跨模块不焊死（与 `projects.owner_user_id` 的写法一致），
引用完整性由 service 层保证。

Revision ID: b5e21a7c9f40
Revises: d7a1e4c93b02
Create Date: 2026-08-28 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b5e21a7c9f40"
down_revision: str | None = "d7a1e4c93b02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "content_revisions",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("field_path", sa.String(length=512), nullable=False),
        sa.Column("old_value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("new_value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("undone_batch_id", sa.Uuid(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_content_revisions")),
    )
    op.create_index(
        op.f("ix_content_revisions_deleted_at"), "content_revisions", ["deleted_at"], unique=False
    )
    op.create_index(
        op.f("ix_content_revisions_org_id"), "content_revisions", ["org_id"], unique=False
    )
    op.create_index(
        op.f("ix_content_revisions_project_id"), "content_revisions", ["project_id"], unique=False
    )
    op.create_index(
        op.f("ix_content_revisions_batch_id"), "content_revisions", ["batch_id"], unique=False
    )
    op.create_index(
        op.f("ix_content_revisions_undone_batch_id"),
        "content_revisions",
        ["undone_batch_id"],
        unique=False,
    )
    # 变更历史页的主查询：本项目 + 按时间倒序
    op.create_index(
        "ix_content_revisions_project_created",
        "content_revisions",
        ["project_id", "created_at"],
        unique=False,
    )
    # 撤销时按批取行，以及"这批被撤销过没有"
    op.create_index(
        "ix_content_revisions_org_batch",
        "content_revisions",
        ["org_id", "batch_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_content_revisions_org_batch", table_name="content_revisions")
    op.drop_index("ix_content_revisions_project_created", table_name="content_revisions")
    op.drop_index(
        op.f("ix_content_revisions_undone_batch_id"), table_name="content_revisions"
    )
    op.drop_index(op.f("ix_content_revisions_batch_id"), table_name="content_revisions")
    op.drop_index(op.f("ix_content_revisions_project_id"), table_name="content_revisions")
    op.drop_index(op.f("ix_content_revisions_org_id"), table_name="content_revisions")
    op.drop_index(op.f("ix_content_revisions_deleted_at"), table_name="content_revisions")
    op.drop_table("content_revisions")
