"""资产直传三段式流程。"""

from __future__ import annotations

import httpx
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration

A = "/api/v1/assets"


async def _ticket(client: AsyncClient, **over: object) -> dict:
    payload = {"filename": "shot.png", "mime_type": "image/png", "size_bytes": 12}
    payload.update(over)  # type: ignore[arg-type]
    r = await client.post(f"{A}/upload-url", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


async def _put(url: str, data: bytes, content_type: str = "image/png") -> int:
    # 真的把字节传到 MinIO——用 mock 的话预签名 URL 是否可用根本没验证
    async with httpx.AsyncClient(timeout=30) as raw:
        resp = await raw.put(url, content=data, headers={"Content-Type": content_type})
    return resp.status_code


async def test_full_upload_roundtrip(alice: AsyncClient) -> None:
    body = b"hello-bytes!"
    ticket = await _ticket(alice, size_bytes=len(body))
    asset_id = ticket["asset"]["id"]

    assert ticket["asset"]["status"] == "pending"
    assert ticket["asset"]["type"] == "image"

    assert await _put(ticket["upload_url"], body) in (200, 204)

    done = await alice.post(f"{A}/{asset_id}/complete")
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "ready"
    assert done.json()["size_bytes"] == len(body)

    # 下载链接可用且内容一致
    url = (await alice.get(f"{A}/{asset_id}/download-url")).json()["url"]
    async with httpx.AsyncClient(timeout=30) as raw:
        got = await raw.get(url)
    assert got.status_code == 200
    assert got.content == body


async def test_complete_is_idempotent(alice: AsyncClient) -> None:
    body = b"abc"
    ticket = await _ticket(alice, size_bytes=len(body))
    await _put(ticket["upload_url"], body)

    first = await alice.post(f"{A}/{ticket['asset']['id']}/complete")
    second = await alice.post(f"{A}/{ticket['asset']['id']}/complete")

    assert first.status_code == second.status_code == 200
    assert second.json()["status"] == "ready"


async def test_complete_without_upload_fails(alice: AsyncClient) -> None:
    """客户端没真传就 complete，必须拒绝。

    只信客户端"我传完了"，后面的生成任务会拿到不存在的文件，
    而且报错时完全看不出问题出在上传环节。
    """
    ticket = await _ticket(alice)
    r = await alice.post(f"{A}/{ticket['asset']['id']}/complete")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "asset.upload.checksum_mismatch"


async def test_size_mismatch_rejected(alice: AsyncClient) -> None:
    """声称 100 字节实际传 3 字节 —— 传了一半断线就是这个表现。"""
    ticket = await _ticket(alice, size_bytes=100)
    await _put(ticket["upload_url"], b"abc")

    r = await alice.post(f"{A}/{ticket['asset']['id']}/complete")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "asset.upload.checksum_mismatch"

    # 坏对象不能留在桶里占空间，记录要标记为 failed
    from sqlalchemy import select

    from apps.api.core.db import get_session_factory
    from apps.api.modules.asset import storage
    from apps.api.modules.asset.models import Asset

    async with get_session_factory()() as s:
        row = (await s.execute(select(Asset).where(Asset.id == ticket["asset"]["id"]))).scalar_one()

    assert row.status == "failed"
    assert await storage.head_object(key=row.storage_key) is None


async def test_unsupported_mime_rejected(alice: AsyncClient) -> None:
    """白名单而非黑名单：能上传什么必须显式列举。"""
    r = await alice.post(
        f"{A}/upload-url",
        json={
            "filename": "evil.sh",
            "mime_type": "application/x-sh",
            "size_bytes": 10,
        },
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "provider.params.invalid"


async def test_oversized_upload_rejected(alice: AsyncClient) -> None:
    r = await alice.post(
        f"{A}/upload-url",
        json={
            "filename": "huge.mp4",
            "mime_type": "video/mp4",
            "size_bytes": 10 * 1024 * 1024 * 1024,
        },
    )
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "asset.upload.too_large"


async def test_pending_assets_hidden_from_list(alice: AsyncClient) -> None:
    """没传完的资产不该出现在资产库里。"""
    ticket = await _ticket(alice)
    ids = {a["id"] for a in (await alice.get(A)).json()["items"]}
    assert ticket["asset"]["id"] not in ids


async def test_download_url_denied_before_ready(alice: AsyncClient) -> None:
    ticket = await _ticket(alice)
    r = await alice.get(f"{A}/{ticket['asset']['id']}/download-url")
    assert r.status_code == 404


async def test_upload_requires_auth(client: AsyncClient) -> None:
    r = await client.post(
        f"{A}/upload-url",
        json={"filename": "a.png", "mime_type": "image/png", "size_bytes": 1},
    )
    assert r.status_code == 401
