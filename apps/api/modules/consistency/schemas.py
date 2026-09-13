from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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
    # "api" = 平台 Provider 画的；"local" = 用户自己电脑上的 Codex 画的。
    # 用户钉上去的那种没人画，为 null。两条生成路径的代价承担者不同
    # （平台上游成本 vs 用户自己的订阅额度），界面必须能分辨。
    image_source: str | None = None


class RenderIn(BaseModel):
    """出图请求体。**没有提示词。**

    提示词由提示词 Agent 合成、由系统注入风格词并校验是否被原样保留
    （ADR-036 / 17_ConsistencyEngine §4），前端传不了也不该传。

    请求体整体可省略：老前端不带 body 时按 `api` + 自动准备提示词走，
    行为与接入前逐字相同（只是词换成了新 Agent 产出的）。
    """

    # 字符串而不是枚举：来源会随本机能力增加，前端按值分派，
    # 加一种不该要求两边同时发版。合法值在 `render.IMAGE_SOURCES` 里校验，
    # 不合法给 422。
    source: str = "api"

    #: 用哪一次准备好的提示词出图。
    #:
    #: 界面上用户能先 `POST /prompts/{kind}/{subject_key}` 看一眼词、改一改
    #: 创作要求，再点生成——那时点名的就是他刚看过的那一份，而不是后端
    #: 重新准备的另一份。不给就自动准备/复用（见 `prompting.resolve_for_render`）。
    #:
    #: 指定的那一份必须属于本项目、本对象、跑成功过、且依据的内容没变过，
    #: 四条缺一不可，否则拒绝出图（`prompt.run.mismatch` / `prompt.run.stale`）。
    prompt_run_id: uuid.UUID | None = None

    #: 这次还想要什么，比如"让他侧身一点"。**只有创作要求，没有风格词。**
    #: 它进摘要，所以换一句话就是另一份提示词，不会串用上一次的。
    instruction: str = Field(default="", max_length=2000)


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
