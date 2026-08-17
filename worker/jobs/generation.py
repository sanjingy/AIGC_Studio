"""真实生成任务：出图。

流程：Gateway 出图 → 下载 → 交给 asset service 落库落存储。

必须转存到自己的对象存储，不能直接把上游 URL 存进库——
DashScope 的图片链接只有 24 小时有效期，直接存等于资产库里
躺着一堆明天就打不开的死链。
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from adapters.providers.base import ImageRequest
from apps.api.core.db import session_scope
from apps.api.core.errors import AppError
from apps.api.core.logging import get_logger
from apps.api.modules.asset import service as asset_service
from apps.api.modules.gateway import service as gateway

log = get_logger(__name__)

DOWNLOAD_TIMEOUT_SECONDS = 120
MAX_IMAGE_BYTES = 32 * 1024 * 1024


async def generate_image(payload: dict[str, Any], *, org_id: uuid.UUID) -> dict[str, Any]:
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        raise AppError("provider.params.invalid", message="prompt 不能为空")

    result = await gateway.generate_image(
        ImageRequest(
            prompt=prompt,
            negative_prompt=str(payload.get("negative_prompt", "")),
            size=str(payload.get("size", "1024*1024")),
            n=int(payload.get("n", 1)),
            seed=payload.get("seed"),
        )
    )

    raw_project = payload.get("project_id")
    project_id = uuid.UUID(str(raw_project)) if raw_project else None

    asset_ids: list[str] = []
    for index, url in enumerate(result.urls):
        data = await _download(url)
        asset_id = uuid.uuid4()
        async with session_scope() as db:
            row = await asset_service.register_generated(
                db,
                org_id=org_id,
                project_id=project_id,
                filename=f"generated_{index}.png",
                storage_key=f"{org_id}/{asset_id}/generated_{index}.png",
                mime_type="image/png",
                data=data,
                metadata={
                    "model_id": result.model_id,
                    "prompt": prompt[:2000],
                    # 上游若改写了提示词必须记下来，否则不知道画出来的
                    # 东西究竟是按什么描述生成的
                    "actual_prompt": (result.actual_prompts[index:] or [""])[0][:2000],
                },
            )
        asset_ids.append(str(row.id))

    return {
        "asset_ids": asset_ids,
        "model_id": result.model_id,
        "actual_prompts": result.actual_prompts,
    }


async def _download(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SECONDS) as client:
        resp = await client.get(url)
    if resp.status_code != 200:
        raise AppError("provider.unavailable", message=f"下载生成结果失败 HTTP {resp.status_code}")
    data = resp.content
    if len(data) > MAX_IMAGE_BYTES:
        raise AppError("asset.upload.too_large", message=f"生成结果 {len(data)} 字节过大")
    return data
