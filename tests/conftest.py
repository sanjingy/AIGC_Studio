from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

os.environ.setdefault("ENV", "test")


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
