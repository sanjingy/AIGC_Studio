from __future__ import annotations

import uuid
from datetime import datetime

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
