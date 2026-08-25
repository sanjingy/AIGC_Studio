"""用户上传 Skill 的 ORM。模块私有。

ADR-026：本轮只做到"能传、能选"，**不接运行时**——生产流程仍走
`orchestrator._NEXT` 的硬编码阶段图。所以这张表现在是一份纯记录：
存住原始 YAML 与校验结论，等阶段图随 M2 稳定后再接线。

校验本身复用 `skills/spec.py` 的白名单规则（04_SkillSpec.md §7），
不在这里另起一套——那套规则本来就是为不可信输入设计的。
"""

from __future__ import annotations

import uuid

from sqlalchemy import Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.core.models import OrgEntity

# 校验结论。invalid 的记录**照样入库**：用户需要看到自己传的东西
# 错在哪，把它丢掉只会得到一个"传了没反应"的黑洞。
SKILL_STATUSES = ("valid", "invalid")


class OrgSkill(OrgEntity):
    __tablename__ = "org_skills"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)

    # 原始上传内容原样保存：校验规则以后会变，重新校验要拿得到原文。
    # 上限由上传侧（后续 Task）按 skills/registry.py 的 MAX_SPEC_BYTES 卡，
    # 不在列上写死长度。
    spec_yaml: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(String(8), nullable=False, default="invalid")
    validation_errors: Mapped[str | None] = mapped_column(Text, nullable=True)

    uploaded_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)

    __table_args__ = (
        # 列表页主查询：本租户 + 按时间倒序，与 projects 一致
        Index("ix_org_skills_org_created", "org_id", "created_at"),
    )
