"""scene consistency profiles

场景资产包。形状照 `character_profiles`（22dbb7fcecbc）抄，
因为场景一致性走的就是角色那条已经验证过的路：结构化字段 → 提示词
按字段拼装 → 基准参考图 → 后续镜头以它为准。

顺带给 `shot_conditioning` 加一列 `scene_profile_id`：那张表回答的是
"这一镜拿什么去生成的"，场景现在也进提示词了，不记下来这个答案就是残的。
可空，存量行不需要回填。

Revision ID: d7a1e4c93b02
Revises: c432be4bec40
Create Date: 2026-08-26 03:10:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d7a1e4c93b02"
down_revision: str | None = "c432be4bec40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scene_profiles",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("ref", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("spatial_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("base_reference_asset_id", sa.Uuid(), nullable=True),
        sa.Column("consistency_tier", sa.String(length=4), nullable=False),
        sa.Column(
            "reference_embedding", pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=True
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scene_profiles")),
    )
    op.create_index(
        op.f("ix_scene_profiles_deleted_at"), "scene_profiles", ["deleted_at"], unique=False
    )
    op.create_index(op.f("ix_scene_profiles_org_id"), "scene_profiles", ["org_id"], unique=False)
    op.create_index(
        op.f("ix_scene_profiles_project_id"), "scene_profiles", ["project_id"], unique=False
    )
    op.create_index(
        "ix_scene_profiles_project_ref",
        "scene_profiles",
        ["project_id", "ref", "version"],
        unique=False,
    )

    op.add_column("shot_conditioning", sa.Column("scene_profile_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    # 每个 migration 必须可回滚且经过测试（09_Database.md 第 14 节）
    op.drop_column("shot_conditioning", "scene_profile_id")
    op.drop_index("ix_scene_profiles_project_ref", table_name="scene_profiles")
    op.drop_index(op.f("ix_scene_profiles_project_id"), table_name="scene_profiles")
    op.drop_index(op.f("ix_scene_profiles_org_id"), table_name="scene_profiles")
    op.drop_index(op.f("ix_scene_profiles_deleted_at"), table_name="scene_profiles")
    op.drop_table("scene_profiles")
