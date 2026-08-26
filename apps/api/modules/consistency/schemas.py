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

    # 用户自己钉的基准图没有任务（那条路径不建任务也不花钱），所以可空。
    # 前端的重试按钮必须靠它 + `source` 判断，不能默认每条记录都能重试。
    task_id: uuid.UUID | None
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
    # "generated" = 真跑过一次生成，扣过 Credits；
    # "assigned"  = 用户指定的一张既有资产，一分钱没花。
    # 界面上两者长得一样，计费语义相反，所以必须透出来。
    source: str


class BaseImageIn(BaseModel):
    """把哪张资产钉成基准图。

    入参只有 asset_id：这条路径不生成任何东西，所以没有提示词、没有
    尺寸、没有种子。图片是用户自己给的，系统一个字都不该往里加。
    """

    asset_id: uuid.UUID


class BaseImageOut(BaseModel):
    """钉完之后的回执。

    刻意不是 `TaskOut`：那个形状里的 `estimated_cost` / `attempt` /
    `status` 一个都不适用——这里没有任务在跑，也没有钱被预扣。
    """

    model_config = ConfigDict(from_attributes=True)

    subject_kind: str
    subject_ref: str
    asset_id: uuid.UUID
    updated_at: datetime
