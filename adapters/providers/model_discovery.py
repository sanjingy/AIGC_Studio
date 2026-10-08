"""Read model IDs from an OpenAI-compatible endpoint without a generation request."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

import httpx

from adapters.providers import endpoint_url
from apps.api.core.errors import AppError
from apps.api.modules.gateway.probe import redact

MAX_RESPONSE_BYTES = 1_048_576
MAX_DISCOVERED_MODELS = 500


def normalize_api_base(raw: str) -> str:
    """Accept a base URL or a standard operation URL; preserve custom base paths.

    `/responses` is stripped too: users copy it from Codex-style docs. Stripping it does
    not mean that API is used -- calls still follow the connection protocol.
    """
    base = endpoint_url.normalize_base_url(raw)
    for suffix in ("/chat/completions", "/images/generations", "/responses", "/models"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return base if urlsplit(base).path else f"{base}/v1"


async def discover_models(*, base_url: str, api_key: str) -> list[str]:
    base = normalize_api_base(base_url)
    await endpoint_url.assert_public_host(base)
    url = f"{base}/models"
    try:
        async with (
            httpx.AsyncClient(timeout=15, follow_redirects=False) as client,
            client.stream("GET", url, headers={"Authorization": f"Bearer {api_key}"}) as response,
        ):
            if response.status_code in (401, 403):
                raise AppError(
                    "common.validation_failed", message="API Key 无效或没有读取模型列表的权限"
                )
            if response.status_code != 200:
                raise AppError(
                    "common.validation_failed",
                    message="无法读取模型列表，请检查请求地址，或在高级设置中手动填写模型 ID",
                )
            payload = bytearray()
            async for chunk in response.aiter_bytes():
                payload.extend(chunk)
                if len(payload) > MAX_RESPONSE_BYTES:
                    raise AppError("common.validation_failed", message="模型列表响应过大")
        data: Any = json.loads(payload)
    except (httpx.HTTPError, ValueError) as exc:
        raise AppError(
            "common.validation_failed",
            message="读取模型列表失败，请检查地址和连接，或在高级设置中手动填写模型 ID",
        ) from exc
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise AppError("common.validation_failed", message="此地址没有返回兼容的模型列表")
    ids: list[str] = []
    for item in items:
        value = item.get("id") if isinstance(item, dict) else None
        if (
            isinstance(value, str)
            and value.strip()
            and len(value.strip()) <= 128
            and not any(c.isspace() or not c.isprintable() for c in value.strip())
            and redact(value, api_key) == value
            and value.strip() not in ids
        ):
            ids.append(value.strip())
            if len(ids) >= MAX_DISCOVERED_MODELS:
                break
    if not ids:
        raise AppError("common.validation_failed", message="没有读取到可选模型，请手动填写模型 ID")
    return ids
