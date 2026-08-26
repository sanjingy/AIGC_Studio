from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RenderOut(BaseModel):
    """一次出图的对外视图。

    状态字段直接来自 `tasks`，不在这里做任何加工——前端拿到的进度
    必须和任务中心、SSE 推的是同一份（ADR-008）。
    """

    model_config = ConfigDict(from_attributes=True)

    task_id: uuid.UUID
    # "character" / "scene" / "shot"。留成字符串而不是枚举：subject 的
    # 种类会随出图能力增加，前端按值分派，加一种不该要求两边同时发版。
    subject_kind: str
    # 角色立绘和场景参考图才有（取各自的 ref）；分镜出图为 null
    subject_ref: str | None
    # 分镜出图才有；角色立绘和场景参考图为 null
    shot_index: int | None
    status: str
    progress: int
    error_code: str | None
    # 出成功了才有。取图 URL 走 /assets/{id}/download-url，
    # 预签名链接有有效期，不能提前签好塞进列表里。
    asset_id: uuid.UUID | None
    created_at: datetime
