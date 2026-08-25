"""Skill 上传的 API 契约。"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from apps.api.modules.skill.service import MAX_SPEC_BYTES


class SkillUploadIn(BaseModel):
    """一份 Skill YAML 的原文。

    走 JSON 而不是 multipart：内容就是一段文本，前端把 `.yaml` 读成字符串
    发过来即可，省掉一套文件上传的处理。上限按字节在 service 里卡
    （`MAX_SPEC_BYTES`），这里的字符数上限只是第一道粗筛——一个 UTF-8
    字符最多 4 字节，所以字符数放到上限的整数倍才不会误伤纯中文的 spec。
    """

    spec_yaml: str = Field(min_length=1, max_length=MAX_SPEC_BYTES)


class SkillOut(BaseModel):
    id: uuid.UUID
    name: str
    version: str
    #: valid | invalid。invalid 的记录照样返回，错误在 validation_errors 里
    status: str
    validation_errors: str | None
    created_at: datetime

    #: 下面几个只有 status=valid 才有值
    skill_id: str | None
    route: str | None
    stage_count: int
    gates: list[str]
    #: spec 引用了但 Agent 注册表里没有的 id。不影响 status，只是提醒
    missing_agents: list[str]

    #: 恒为 False。ADR-026：本轮只做到"能传、能选"，运行时没有接线。
    #: 由后端给而不是前端写死一句文案——将来真接上了，前端不需要改。
    runtime_wired: bool = False


class SkillList(BaseModel):
    items: list[SkillOut]


class SkillSpecOut(BaseModel):
    """上传时的原文，供用户核对。"""

    id: uuid.UUID
    spec_yaml: str
