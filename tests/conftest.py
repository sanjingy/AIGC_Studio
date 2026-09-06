from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

# 必须用赋值而非 setdefault：compose 已经把 ENV=local 注入了环境变量，
# setdefault 不会覆盖。之前就是这个原因导致整个测试套件在打真实上游——
# 每跑一轮都在花钱，且把网络抖动带进了测试。
os.environ["ENV"] = "test"

# 预签名 URL 的对外地址默认是 localhost:9000，那是给浏览器用的。
# 测试跑在容器内，那里的 localhost 指向 API 自己，连不到 MinIO。
# SigV4 把 host 算进签名，所以不能改 URL 的主机名——只能让签名本身
# 用容器内可达的地址。
#
# 这样仍然真实覆盖了：预签名生成、真实 PUT 上传、HEAD 校验、GET 下载。
# 未覆盖的只有"对外主机名是否配对"，那是部署配置问题不是代码问题。
#
# 用赋值而非 setdefault：compose 已经把这个变量注入了环境，setdefault 不会生效。
# 对齐到内部地址而非写死主机名，这样在任何环境下都成立——
# 测试进程与 API 同处一地，内部地址按定义就是可达的。
if _internal := os.environ.get("S3_ENDPOINT_URL"):
    os.environ["S3_PUBLIC_ENDPOINT_URL"] = _internal

# 上面改了环境变量，缓存的 Settings 必须作废，否则读到的还是旧值。
from apps.api.core.config import get_settings

get_settings.cache_clear()


@pytest.fixture(autouse=True)
async def _reset_connection_pools() -> AsyncIterator[None]:
    """每个用例后释放连接池。

    引擎和 Redis 池是模块级单例，会绑定到创建它们的那个 event loop。
    pytest-asyncio 每个用例新建一个 loop，复用上一轮的连接会报
    "attached to a different loop"。用例间释放掉最省心。
    """
    yield
    from apps.api.core.db import dispose_engine
    from apps.api.core.redis import close_redis

    await dispose_engine()
    await close_redis()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from apps.api.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """直连数据库的会话，用于断言落库结果。"""
    from apps.api.core.db import get_session_factory

    async with get_session_factory()() as session:
        yield session


def unique_email() -> str:
    """每个用例用独立邮箱，避免相互污染，也不需要清库。"""
    return f"t-{uuid.uuid4().hex[:12]}@example.com"


@pytest.fixture
def credentials() -> dict[str, str]:
    return {
        "email": unique_email(),
        "password": "correct-horse-9",
        "display_name": "测试用户",
    }


async def new_client(name: str = "用户") -> AsyncClient:
    """新建一个已登录的客户端，带独立的 org。

    租户隔离测试需要两个互不相干的账号，所以做成可多次调用的工厂
    而不是 fixture。
    """
    from apps.api.main import app

    ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    r = await ac.post(
        "/api/v1/auth/register",
        json={
            "email": unique_email(),
            "password": "correct-horse-9",
            "display_name": name,
        },
    )
    assert r.status_code == 201, r.text
    return ac


@pytest.fixture
async def alice() -> AsyncIterator[AsyncClient]:
    ac = await new_client("Alice")
    yield ac
    await ac.aclose()


@pytest.fixture
async def bob() -> AsyncIterator[AsyncClient]:
    """与 alice 属于不同租户，用于越权测试。"""
    ac = await new_client("Bob")
    yield ac
    await ac.aclose()


# ---------------------------------------------------------------- 编排门助手
#
# 门的**数量和顺序会变**（2026-09-05 由 2 道变 4 道，ADR-037），而"跑到某道
# 门"是十来个用例的共同前置。每个文件各写一份"推进 → 通过 → 再推进"就等于
# 把门的数量硬编码进十来处，加一道门要改十来个文件——上一次就是这样。
#
# 这里只写"一直推进并通过遇到的每一道门，直到停在指定的那道门上"，
# 它对门的数量不敏感。

PROJECTS = "/api/v1/projects"


async def pending_approval(client: AsyncClient, project_id: str) -> dict[str, Any] | None:
    rows = (await client.get(f"{PROJECTS}/{project_id}/approvals")).json()
    return next((dict(r) for r in rows if r["status"] == "pending"), None)


async def advance_to_gate(
    client: AsyncClient,
    project_id: str,
    gate: str,
    *,
    user_input: str = "",
    max_gates: int = 6,
) -> dict[str, Any]:
    """一路推进，通过途中每一道门，停在 `gate` 上（不通过它）。

    返回停下来的那一次 advance 响应。走不到目标门就断言失败——静默返回
    会让后面的用例在一个完全不同的阶段上做断言，失败信息指向错误的地方。
    """
    body = dict(
        (
            await client.post(
                f"{PROJECTS}/{project_id}/advance?to_gate=true",
                json={"user_input": user_input},
            )
        ).json()
    )
    for _ in range(max_gates):
        if body.get("gate_opened") == gate:
            return body
        current = await pending_approval(client, project_id)
        assert current is not None, f"没走到 {gate} 门就停住了：{body}"
        r = await client.post(
            f"{PROJECTS}/{project_id}/approvals/{current['id']}", json={"decision": "approved"}
        )
        assert r.status_code == 200, r.text
        body = dict(
            (
                await client.post(
                    f"{PROJECTS}/{project_id}/advance?to_gate=true", json={"user_input": ""}
                )
            ).json()
        )
    raise AssertionError(f"连过 {max_gates} 道门都没停在 {gate} 上")


async def approve_gate(client: AsyncClient, project_id: str, gate: str) -> None:
    """通过当前挂着的那道门，并断言它就是期望的那一道。"""
    current = await pending_approval(client, project_id)
    assert current is not None, f"{gate} 门没开"
    assert current["gate"] == gate, f"当前挂着的是 {current['gate']}，不是 {gate}"
    r = await client.post(
        f"{PROJECTS}/{project_id}/approvals/{current['id']}", json={"decision": "approved"}
    )
    assert r.status_code == 200, r.text
