"""Redis 客户端。

Redis 在本系统里承担四件事，不要混用 key 前缀：
  q:*        Arq 队列
  cache:*    缓存
  events:*   SSE 事件流（Redis Stream，见 20_Realtime.md）
  cb:*       Provider 熔断状态（必须全 Worker 共享，见 21_ErrorTaxonomy.md 第 4 节）
  idem:*     幂等键
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from redis.asyncio import ConnectionPool, Redis
from redis.asyncio.connection import Connection

from apps.api.core.config import get_settings

# Redis 只在类型存根里是泛型，运行时不是——直接写 `Redis[str]` 会在
# 导入阶段抛 "is not a generic class"。必须按上下文分开定义。
# ConnectionPool 的泛型参数是连接类型，Redis 的才是响应类型；
# 池上设了 decode_responses=True，所以响应是 str。
if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis

_pool: ConnectionPool[Connection] | None = None


def get_redis() -> RedisClient:
    global _pool
    if _pool is None:
        _pool = ConnectionPool.from_url(
            get_settings().redis_url,
            decode_responses=True,
            max_connections=50,
        )
    # 类型标注推不出 decode_responses 是运行期参数，这里显式收敛。
    return cast(RedisClient, Redis(connection_pool=_pool))


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
