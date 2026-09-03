"""字段级变更记录的 ORM。模块私有。

一张表回答三个问题：**谁、在什么时候、把哪个字段从什么改成了什么**。
它不是日志的美化版——`POST /revisions/{batch_id}/undo` 直接把这些行
反过来重放，所以 `old_value` / `new_value` 是**可执行的数据**，
不是给人看的描述。写坏它等于写坏撤销。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Index, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.core.models import OrgEntity

# 变更的来源。**新增来源要同步扩这里**，不要在业务代码里裸写字符串。
#
# user_edit     用户在界面上直接改字段（本轮唯一的人工写入来源）
# undo          撤销某一批。撤销本身也是一次变更，同样要可审计、可再撤销
# agent_revise  聊天修订（`POST /revise`）——**尚未接入**，见 ADR-029 的"代价"
SOURCES = ("user_edit", "undo", "agent_revise")


class ContentRevision(OrgEntity):
    """一次字段级改动。一次 UI 操作产生一批，同批共享 `batch_id`。

    批不单独建表：一批的元信息（reason / actor / source / 时间）在每一行上
    都是同一个值，冗余几十字节换掉一张表和一次 join。真正的代价是
    "一批必须整体写入或整体不写"——而它本来就在一个事务里。
    """

    __tablename__ = "content_revisions"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)

    # 一次 UI 操作一个。撤销以它为单位——ReelBench 的"撤销以批为单位"
    # 说的就是这个粒度：用户改了 5 个字段点一次保存，撤销要把 5 个一起退回去。
    batch_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)

    # 改的是哪个阶段产出（plot_index / screenplay / characters / scenes / storyboard）。
    # 与 `current_state_json` 的键名一致，不另起名字。
    role: Mapped[str] = mapped_column(String(32), nullable=False)

    # RFC 6901 JSON Pointer，相对于该 role 的整块产出，如 /characters/2/hair
    field_path: Mapped[str] = mapped_column(String(512), nullable=False)

    # 旧值 / 新值。**允许是 JSON null**——`{"distinctive": null}` 与
    # "这一列没填" 是两件事，混成 SQL NULL 会让撤销把一个存在的字段写没。
    # SQLAlchemy 的 JSON 类型默认 `none_as_null=False`，Python None 落成
    # JSON 的 null，正是要的语义，所以这两列都是 NOT NULL。
    old_value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    new_value: Mapped[Any] = mapped_column(JSONB, nullable=False)

    # 用户填的改动原因。ReelBench 有这一栏，值得抄：三个月后回看
    # "为什么把主角从 16 岁改成 22 岁"，只有这一栏答得上来。
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # 谁改的。可空：以后接入 `agent_revise` 时，改动的执行者是模型不是人。
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    source: Mapped[str] = mapped_column(String(32), nullable=False, default="user_edit")

    # 只有 source='undo' 的行才有值：这一批撤销的是哪一批。
    # 它同时是"这一批已经被撤销过了"的唯一判据——不在被撤销的那批上打标记，
    # 因为那需要回头改历史行，而历史行一旦写下就不该再动。
    undone_batch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)

    __table_args__ = (
        # 变更历史页的主查询：本项目 + 按时间倒序
        Index("ix_content_revisions_project_created", "project_id", "created_at"),
        # 撤销时按批取行，以及"这批被撤销过没有"
        Index("ix_content_revisions_org_batch", "org_id", "batch_id"),
    )
