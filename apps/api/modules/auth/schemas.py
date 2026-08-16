from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)

    @field_validator("password")
    @classmethod
    def _strength(cls, v: str) -> str:
        # 只挡最弱的一类：纯数字/纯字母。不做复杂度剧场
        # （强制符号只会让用户写 Password1! 然后到处复用）。
        if v.isdigit() or v.isalpha():
            raise ValueError("密码需要同时包含字母和数字")
        return v

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("display_name")
    @classmethod
    def _trim_name(cls, v: str) -> str:
        name = v.strip()
        if not name:
            raise ValueError("昵称不能为空")
        return name


class LoginIn(BaseModel):
    email: EmailStr
    password: str

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        return v.strip().lower()


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    email: str
    display_name: str
    role: str
    status: str
    created_at: datetime


class SessionOut(BaseModel):
    """令牌走 httpOnly Cookie，不出现在响应体里——避免被 XSS 读走。"""

    user: UserOut
    access_expires_in: int
