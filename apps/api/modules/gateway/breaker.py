"""Provider 熔断器（21_ErrorTaxonomy.md §4）。

**状态存 Redis，全 Worker 共享。**
存进程内存的话，每个 Worker 各自熔断，等于没熔断——
8 个 Worker 会各自把上游打 5 次才认输，总共 40 次无效请求。

**但"共享"的边界是账号，不是 Provider**（ADR-027）：这里所有函数收的都是
:func:`scope` 算出来的作用域 id，平台档一个、每个自带 Key 的 org 各一个。
理由见 `scope` 的注释。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

from apps.api.core.logging import get_logger
from apps.api.core.redis import Keys, get_redis

log = get_logger(__name__)


def _r() -> Any:
    """redis-py 的类型存根同步/异步共用，异步方法被标成
    `Awaitable[int] | int`，直接 await 过不了 mypy strict。
    在这一处收敛掉，不给整个 redis 模块关类型检查。"""
    return cast(Any, get_redis())


WINDOW_SECONDS = 300
FAIL_THRESHOLD = 5  # 窗口内连续失败多少次转 DEGRADED
DOWN_THRESHOLD = 10  # DEGRADED 下再失败多少次转 DOWN
PROBE_INTERVAL_SECONDS = 60  # DOWN 后多久探测一次
RECOVER_SUCCESSES = 3  # 连续成功多少次恢复


def scope(provider_id: str, *, org_id: uuid.UUID | None = None) -> str:
    """熔断作用域 id。平台档就是 provider_id，自带 Key 的 org 各算各的。

    合起来算是错的，两个方向都错：

    - 某个用户的 Key 被上游封了、欠费了、把配额打满了，连着失败 5 次就会
      把这家 Provider 整个熔断掉——**用平台 Key 的所有其他用户跟着降级**。
      一个人的账号问题不该有这种放大倍数。
    - 反过来，平台自己的账号出问题时把自带 Key 的用户也拦住同样没道理：
      他用的是另一个账号，那个账号好好的。

    熔断计的是"这个账号在这家上游还能不能用"，而账号的粒度就是这里的 org。
    """
    return provider_id if org_id is None else f"{provider_id}@org:{org_id}"


class State(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"  # 权重降低但仍发探测流量
    DOWN = "down"  # 不发流量，定期探测


@dataclass(frozen=True, slots=True)
class Snapshot:
    state: State
    failures: int
    successes: int
    opened_at: float


async def snapshot(provider_id: str) -> Snapshot:
    """`provider_id` 收的是 :func:`scope` 的结果，不一定等于 Provider 本身。"""
    raw = await _r().hgetall(Keys.circuit_breaker(provider_id))
    return Snapshot(
        state=State(raw.get("state", State.HEALTHY)),
        failures=int(raw.get("failures", 0)),
        successes=int(raw.get("successes", 0)),
        opened_at=float(raw.get("opened_at", 0)),
    )


async def allows(provider_id: str) -> bool:
    """现在能不能给这个 Provider 发流量。"""
    snap = await snapshot(provider_id)
    if snap.state is State.HEALTHY:
        return True
    if snap.state is State.DEGRADED:
        return True  # 降权但仍探测，靠 resolve 的排序体现降权
    # DOWN：只在探测窗口放行一次
    return time.time() - snap.opened_at >= PROBE_INTERVAL_SECONDS


async def record_success(provider_id: str) -> None:
    r = _r()
    key = Keys.circuit_breaker(provider_id)
    snap = await snapshot(provider_id)

    if snap.state is State.HEALTHY:
        if snap.failures:
            await r.hset(key, "failures", "0")
        return

    successes = await r.hincrby(key, "successes", 1)
    if successes >= RECOVER_SUCCESSES:
        await r.hset(key, mapping={"state": State.HEALTHY.value, "failures": "0", "successes": "0"})
        log.info("breaker.recovered", provider_id=provider_id)
    elif snap.state is State.DOWN:
        # 探测成功先回到 DEGRADED，不直接放全量流量
        await r.hset(key, "state", State.DEGRADED.value)
        log.info("breaker.probing", provider_id=provider_id)


async def record_failure(provider_id: str) -> None:
    r = _r()
    key = Keys.circuit_breaker(provider_id)
    failures = await r.hincrby(key, "failures", 1)
    await r.hset(key, "successes", "0")
    await r.expire(key, WINDOW_SECONDS * 4)

    snap = await snapshot(provider_id)
    if snap.state is State.HEALTHY and failures >= FAIL_THRESHOLD:
        await r.hset(key, mapping={"state": State.DEGRADED.value, "opened_at": str(time.time())})
        log.warning("breaker.degraded", provider_id=provider_id, failures=failures)
    elif snap.state is State.DEGRADED and failures >= DOWN_THRESHOLD:
        await r.hset(key, mapping={"state": State.DOWN.value, "opened_at": str(time.time())})
        log.error("breaker.down", provider_id=provider_id, failures=failures)


async def reset(provider_id: str) -> None:
    await _r().delete(Keys.circuit_breaker(provider_id))
