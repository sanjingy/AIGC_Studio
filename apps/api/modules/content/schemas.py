"""字段级编辑与变更历史的请求/响应体。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 一批的上限。不是业务阈值（所以不进 pricing_rules），是护栏：
# 一次 UI 操作改上千个字段说明前端在批量拼装，那条路要走批量端点，
# 不该借道人工编辑——它没有预算检查也没有幂等键。
MAX_PATCHES_PER_BATCH = 200
MAX_REASON_CHARS = 500


class PatchOp(BaseModel):
    """一处字段改动。**只有"替换"一种语义**，见 patching.py 的规则 1。

    `value` 没有默认值，所以它是必填的——这和"传了 null"是两件事：
    `{"path": "/x", "value": null}` 是把这个字段改成 null，
    漏传 value 是请求写错了，必须报 422 而不是默默当成 null。
    """

    path: str = Field(min_length=2, max_length=512)
    value: Any


class OutputPatchIn(BaseModel):
    patches: list[PatchOp] = Field(min_length=1, max_length=MAX_PATCHES_PER_BATCH)
    reason: str | None = Field(default=None, max_length=MAX_REASON_CHARS)

    @field_validator("reason")
    @classmethod
    def _trim(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip() or None


class UndoIn(BaseModel):
    """撤销也能填原因。空 body 合法，所以整个模型都有默认值。"""

    reason: str | None = Field(default=None, max_length=MAX_REASON_CHARS)

    @field_validator("reason")
    @classmethod
    def _trim(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip() or None


class RevisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    field_path: str
    old_value: Any
    new_value: Any


class RevisionBatchOut(BaseModel):
    batch_id: uuid.UUID
    role: str
    source: str
    reason: str | None
    actor_user_id: uuid.UUID | None
    created_at: datetime
    changes: list[RevisionOut]
    # 这批已经被撤销过了，以及被哪一批撤销的。
    # 前端拿它决定"撤销"按钮还能不能点——不给这个字段，用户只能点下去
    # 吃一个 409。
    undone_by_batch_id: uuid.UUID | None = None
    # 这一批本身是不是一次撤销。撤销可以再被撤销（相当于重做），
    # 所以不能靠 `undone_batch_id is None` 反推。
    undoes_batch_id: uuid.UUID | None = None


class RevisionPage(BaseModel):
    items: list[RevisionBatchOut]
    next_cursor: str | None


class PatchResultOut(BaseModel):
    """写路径的统一返回体。撤销与 PATCH 共用——它们是同一种操作。"""

    batch_id: uuid.UUID
    role: str
    changed: int
    # 改完之后这个 role 的整块产出。前端不必再 GET 一次，
    # 也就不会出现"保存成功但界面还是旧值"的窗口。
    output: dict[str, Any]
    # 因为这次改动而过期的下游阶段。语义与 revise 的同名字段一致。
    stale_roles: list[str]
