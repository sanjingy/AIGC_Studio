"""Gateway 自己的两张表（05_MODEL_GATEWAY.md §6.2 / §8.2）。

在此之前 Gateway 没有任何持久化层——Key 在 billing（`provider_credentials`，
它存在的理由是计费），偏好在 project（`projects.model_preference`）。
这两张表归 Gateway，是因为它们回答的都是"这次该调谁"：

- `org_model_defaults`：组织给某个能力选的默认上游 + 模型 + 计费来源。
  三层解析里的中间一层（项目选择 > **组织默认** > 平台目录默认）。
- `org_provider_connections`：组织自带 Key 的供应商连接（ADR-039），一个 org 多行。
  **没有 capability 列**——能力由每个模型的协议在代码白名单里推出
  （`catalog.PROTOCOLS`），没有这一列，就没有人能往里存 `"tts"`（§5.2 第 2 条）。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, Index, String, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.core.models import OrgEntity

KEY_SOURCES = ("platform", "org")


class OrgModelDefault(OrgEntity):
    """组织默认。一行一个能力，加能力就是加行。

    `model_id` 可空：空表示"这一家的目录默认顺序"，选了上游但没钉具体模型。
    `key_source` 是**用户显式选的计费来源**，不再从"库里有没有 Key"推断——
    存着一把 Key 但暂时想走平台额度，是一个真实的需求。

    `updated_by` 必填：组织默认是会被追责的配置（谁把默认从 flash 改成
    plus，下个月账单翻倍时要查得出来），这是它不做成 JSONB 列的原因之一。
    """

    __tablename__ = "org_model_defaults"

    capability: Mapped[str] = mapped_column(String(32), nullable=False)
    #: 目录里的一家（`provider.deepseek`）或组织连接（`provider.org:<uuid>`，49 字符）
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    key_source: Mapped[str] = mapped_column(String(16), nullable=False)
    updated_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)

    __table_args__ = (
        Index(
            "uq_org_model_defaults_org_capability",
            "org_id",
            "capability",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class OrgProviderConnection(OrgEntity):
    """组织自带 Key 的一个供应商连接（ADR-039 第 1 条）。

    `models` 是 `[{"model_id": str, "protocol": str}]`；协议必须在
    `catalog.PROTOCOLS` 白名单里（保存时校验），能力由协议推出。

    `key_encrypted` / `secret_encrypted` 与 `provider_credentials` 同一套 AES-GCM
    （`core/crypto.py`），明文只在 Gateway 解密后于内存中存在一次。`secret_encrypted`
    留给 AK/SK 类协议（B 批），A 批两个协议都不用。`base_url` 入库前已过
    `endpoint_url.normalize_base_url`，每次调用前还会再做一次解析校验。
    """

    __tablename__ = "org_provider_connections"

    label: Mapped[str] = mapped_column(String(64), nullable=False)
    preset_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    models: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
