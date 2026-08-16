"""Redis 客户端。

Redis 在本系统里承担四件事，不要混用 key 前缀：
  q:*        Arq 队列
  cache:*    缓存
  events:*   SSE 事件流（Redis Stream，见 20_Realtime.md）
  cb:*       Provider 熔断状态（必须全 Worker 共享，见 21_ErrorTaxonomy.md 第 4 节）
  idem:*     幂等键
"""

from __future__ import annotations

from redis.asyncio import ConnectionPool, Redis

from apps.api.core.config import get_settings

# Redis / ConnectionPool 都不是泛型——redis>=5 自带的类型标注里没有类型参数。
# （已废弃的 types-redis 存根把它们标成了泛型，按那个写运行时会直接抛
#  "is not a generic class"。）
# 池上设了 decode_responses=True，所以命令返回的是 str。
RedisClient = Redis

_pool: ConnectionPool | None = None


def get_redis() -> RedisClient:
    global _pool
    if _pool is None:
        _pool = ConnectionPool.from_url(
            get_settings().redis_url,
            decode_responses=True,
            max_connections=50,
        )
    return Redis(connection_pool=_pool)


async def close_redis() -> None:
    global _pool
    if _pool is not None:
        await _pool.disconnect()
    _pool = None


class Keys:
    """集中管理 key 命名，避免各处硬编码字符串拼接。"""

    @staticmethod
    def events(project_id: str) -> str:
        return f"events:project:{project_id}"

    @staticmethod
    def event_seq(project_id: str) -> str:
        return f"events:seq:{project_id}"

    @staticmethod
    def circuit_breaker(provider_id: str) -> str:
        return f"cb:provider:{provider_id}"

    @staticmethod
    def idempotency(key: str) -> str:
        return f"idem:{key}"
