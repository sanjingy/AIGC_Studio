"""一致性引擎的 ORM（17_ConsistencyEngine.md §7）。模块私有。

这不是锦上添花的功能。用户判断产品能不能用的第一标准是
"第 1 个镜头的主角和第 47 个镜头是不是同一个人"，
而废片率是单片成本的唯一主导因素（19_UnitEconomics.md §2.3）。
一致性同时是产品生死线和成本中心。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Index, Integer, Numeric, String, Text, Uuid
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.core.models import OrgEntity

# 分层降级（17_ConsistencyEngine.md §2）。不存在单一银弹。
CONSISTENCY_TIERS = ("L0", "L1", "L2", "L3")

EMBEDDING_DIM = 1024  # DashScope multimodal-embedding-v1


class StyleProfile(OrgEntity):
    """项目级风格锁定。

    风格漂移和角色漂移是**两个独立问题**，很多实现会混淆。
    这张表只管风格：一旦有镜头产出即冻结，改动必须新建版本
    并明确告知用户"已生成的镜头需要重新生成"。
    """

    __tablename__ = "style_profiles"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    base_model: Mapped[str] = mapped_column(String(64), nullable=False)
    # 系统统一注入的风格词。**Agent 不许自己写风格词**——
    # 那是画风漂移的头号来源（17_ConsistencyEngine.md §4）。
    positive_tokens: Mapped[str] = mapped_column(Text, nullable=False, default="")
    negative_tokens: Mapped[str] = mapped_column(Text, nullable=False, default="")

    color_grading: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    line_weight: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    render_mode: Mapped[str] = mapped_column(String(40), nullable=False, default="")

    seed_base: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_style_profiles_project_version", "project_id", "version"),)


class CharacterProfile(OrgEntity):
    """角色资产包。

    外貌拆成结构化字段而不是一段自由文本：
    条件化提示词要按字段拼装，一段散文没法稳定地提取出"发色是什么"。
    """

    __tablename__ = "character_profiles"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    # Agent 输出里用来引用它的短 id，如 lin_shu
    ref: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    appearance_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    base_portrait_asset_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    turnaround_asset_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(Uuid), nullable=False, default=list
    )

    consistency_tier: Mapped[str] = mapped_column(String(4), nullable=False, default="L1")
    # 基准立绘的向量。后续镜头出图后与它比对，得到角色相似度。
    # 这就是 02_Architecture.md 里 pgvector 的真实用途——不是 RAG。
    reference_embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True
    )

    # 冻结后不再改动，后续所有镜头引用这一版
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_character_profiles_project_ref", "project_id", "ref", "version"),)


class SceneProfile(OrgEntity):
    """场景资产包。角色资产包的场景对偶物。

    空间信息拆成结构化字段而不是一段自由文本，理由和角色一样：
    条件化提示词要按字段拼装，一段散文没法稳定地提取出"摄影机站在哪"。

    真正撑起场景一致性的是 `camera_axis` 和 `fixed_references`
    （`agents/schemas.py` 的 `SceneSheet`）。没有摄影主轴，"这个场景的
    正面"就由模型每次自己挑，同一个场景两次生成会朝两个方向；没有固定
    参照物，同一个房间里的书桌这次在左边下次在右边。这两个字段必须真的
    进提示词——只存不用等于场景出图看着和角色出图一样，但没有一致性。
    """

    __tablename__ = "scene_profiles"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    # Agent 输出里用来引用它的短 id，如 gate。分镜表的 `scene_ref` 指的就是它。
    ref: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    spatial_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    base_reference_asset_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    consistency_tier: Mapped[str] = mapped_column(String(4), nullable=False, default="L1")
    # 基准参考图的向量。留给后续的场景相似度度量，用途与角色那一列相同。
    reference_embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True
    )

    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_scene_profiles_project_ref", "project_id", "ref", "version"),)


class ShotConditioning(OrgEntity):
    """一个镜头实际用了什么去生成。

    `resolved_prompt` 必须存全文。没有它，"为什么这一镜崩了"
    永远查不清，也没法复现（17_ConsistencyEngine.md §7）。
    """

    __tablename__ = "shot_conditioning"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    shot_index: Mapped[int] = mapped_column(Integer, nullable=False)

    style_profile_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    character_profile_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(Uuid), nullable=False, default=list
    )
    # 这一镜发生在哪个场景。可空：分镜表没写 scene_ref、或者项目还没有
    # 场景档案时，镜头照出——旧项目不该因为多了一张表就出不了图。
    scene_profile_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    resolved_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    negative_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)

    asset_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    accepted: Mapped[bool] = mapped_column(nullable=False, default=False)

    __table_args__ = (
        Index("ix_shot_conditioning_project_shot", "project_id", "shot_index", "attempt"),
    )


class ShotQualityScore(OrgEntity):
    """量化指标（17_ConsistencyEngine.md §6）。

    QA 不能靠"感觉像"。这些数字同时是三件事的依据：
    自动重试的判据、废片率的数据来源、Agent 回归测试的评分。
    """

    __tablename__ = "shot_quality_scores"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    shot_index: Mapped[int] = mapped_column(Integer, nullable=False)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    metric: Mapped[str] = mapped_column(String(40), nullable=False)
    value: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    threshold: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    passed: Mapped[bool] = mapped_column(nullable=False)

    __table_args__ = (Index("ix_shot_quality_project_shot", "project_id", "shot_index", "metric"),)
