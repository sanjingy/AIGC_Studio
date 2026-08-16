from __future__ import annotations

from httpx import AsyncClient


async def test_healthz_is_dependency_free(client: AsyncClient) -> None:
    """存活探针不能依赖 DB/Redis，否则依赖抖动会导致容器被反复重启。"""
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_trace_id_is_returned(client: AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.headers.get("X-Trace-Id")


async def test_trace_id_is_propagated(client: AsyncClient) -> None:
    resp = await client.get("/healthz", headers={"X-Trace-Id": "abc123"})
    assert resp.headers["X-Trace-Id"] == "abc123"
