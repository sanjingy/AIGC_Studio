from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class UploadRequestIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=3, max_length=120)
    size_bytes: int = Field(gt=0)
    project_id: uuid.UUID | None = None


class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID | None
    #: null = 未分类。归类关系存在单独的表里，不是 assets 的列。
    folder_id: uuid.UUID | None = None
    type: str
    status: str
    filename: str
    mime_type: str
    size_bytes: int | None
    width: int | None
    height: int | None
    duration_ms: int | None
    moderation_status: str
    created_at: datetime


class UploadTicketOut(BaseModel):
    asset: AssetOut
    upload_url: str
    expires_at: datetime


class DownloadUrlOut(BaseModel):
    url: str
    expires_at: datetime


class AssetPage(BaseModel):
    items: list[AssetOut]
    next_cursor: str | None


class StorageUsageOut(BaseModel):
    """资产库用量。字节数与整数百分比都由后端算好。

    百分比不交给前端算：前端一除就是浮点，两处各算一次迟早对不上，
    而"还剩多少"这种数字对不上会直接变成用户投诉。
    """

    used_bytes: int
    #: null = 未配置配额（pricing_rules 里没有这条规则），即不限容量
    quota_bytes: int | None
    free_bytes: int | None
    percent_used: int


class ProfileEntryOut(BaseModel):
    """角色档案 / 场景档案。只读展示，不占容量配额。"""

    project_id: uuid.UUID
    project_title: str
    #: characters / scenes，前端据此选渲染组件
    kind: str
    agent_id: str
    run_id: uuid.UUID
    output: dict[str, Any]
    created_at: datetime
    #: null = 未分类
    folder_id: uuid.UUID | None = None


class CharacterEntryOut(BaseModel):
    """独立角色档案：一段参考描述直接生成的，不挂任何项目。

    `output` 与 ProfileEntryOut 的角色档案是同一个 schema（CharacterSheets），
    前端用同一个组件渲染。
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    #: 当初写的那段参考描述，能回看"照着什么生成的"
    source_text: str
    agent_id: str
    output: dict[str, Any]
    model_id: str | None
    created_at: datetime
    folder_id: uuid.UUID | None = None


class FolderOut(BaseModel):
    id: uuid.UUID
    name: str
    #: 里面有多少条目（资产 + 档案），实时数出来的
    item_count: int
    created_at: datetime


class FolderIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class FolderItemIn(BaseModel):
    """把一个东西归到某个文件夹。`folder_id` 为空表示移出，回到未分类。"""

    #: asset / profile / character
    item_type: str = Field(min_length=1, max_length=16)
    item_id: uuid.UUID
    folder_id: uuid.UUID | None = None


class CharacterFromReferenceIn(BaseModel):
    """一段参考描述 → 一份正式角色档案。

    上限比修订指令（2000 字）宽：那是"改哪里"，这是一份完整设定。
    真正的上限在服务层（MAX_REFERENCE_CHARS），这里只是提前挡掉明显过长的
    请求体，不重复定义业务规则。
    """

    description: str = Field(min_length=1, max_length=6000)
    #: 生成后直接归到这个文件夹；不给就是未分类
    folder_id: uuid.UUID | None = None


class CharacterGeneratedOut(BaseModel):
    entry: CharacterEntryOut
    #: 预扣与实际结算的 Credits。差额已退回可用余额。
    reserved_credits: int
    cost_credits: int


class LibraryOut(BaseModel):
    usage: StorageUsageOut
    assets: list[AssetOut]
    next_cursor: str | None
    profiles: list[ProfileEntryOut]
    characters: list[CharacterEntryOut]
    folders: list[FolderOut]
