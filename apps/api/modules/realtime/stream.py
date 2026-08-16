"""Redis Stream 读写。

事件 ID 直接用 Stream 条目 ID（ADR-018）：它本身单调递增且全局唯一，
`XRANGE (last +` 天然实现"从这个位置之后重放"，不需要自建序号。
"""

from __future__ import annotations

import json
from typing import Any

from apps.api.core.redis import Keys, get_redis

# 每个项目保留的事件条数。够覆盖一次短暂断线重连，
# 超出说明客户端离线太久，让它走全量拉取。
STREAM_MAXLEN = 1000
STREAM_TTL_SECONDS = 86_400


async def publish(*, project_id: str, event_type: str, payload: dict[str, Any]) -> str:
    r = get_redis()
    key = Keys.events(project_id)
    entry_id: str = await r.xadd(
        key,
        {"type": event_type, "data": json.dumps(payload, ensure_ascii=False)},
        maxlen=STREAM_MAXLEN,
        approximate=True,
    )
    await r.expire(key, STREAM_TTL_SECONDS)
    return entry_id


async def read_since(
    *, project_id: str, last_id: str, count: int
) -> list[tuple[str, dict[str, str]]]:
    """读取 last_id 之后的历史事件，用于重连补齐。"""
    r = get_redis()
    # "(" 前缀表示排他，即不含 last_id 本身
    exclusive = f"({last_id}" if last_id and last_id != "0" else "-"
    rows = await r.xrange(Keys.events(project_id), min=exclusive, max="+", count=count)
    return [(entry_id, fields) for entry_id, fields in rows]


async def read_blocking(
    *, project_id: str, last_id: str, block_ms: int
) -> list[tuple[str, dict[str, str]]]:
    """阻塞等待新事件。超时返回空列表，让调用方有机会发心跳。"""
    r = get_redis()
    resp = await r.xread({Keys.events(project_id): last_id}, count=64, block=block_ms)
    if not resp:
        return []
    _key, entries = resp[0]
    return [(entry_id, fields) for entry_id, fields in entries]


async def latest_id(*, project_id: str) -> str:
    """当前流末尾。新连接从这里开始，不重放历史。"""
    r = get_redis()
    rows = await r.xrevrange(Keys.events(project_id), count=1)
    return str(rows[0][0]) if rows else "0-0"


def parse_id(entry_id: str) -> tuple[int, int]:
    """把 `毫秒-序号` 解析成可比较的元组。

    不能直接用字符串比较：位数不同时 "9-0" 会大于 "10-0"。
    """
    ms, _, seq = entry_id.partition("-")
    try:
        return int(ms), int(seq or 0)
    except ValueError:
        return 0, 0


async def cursor_is_fresh(*, project_id: str, entry_id: str) -> bool:
    """判断客户端游标是否还在保留窗口内。

    不在说明它离线太久、中间的事件已被修剪，必须让它全量拉取，
    而不是从现有最老的一条开始装作什么都没丢。
    """
    if entry_id in ("", "0", "0-0"):
        return True

    r = get_redis()
    rows = await r.xrange(Keys.events(project_id), min="-", max="+", count=1)
    if not rows:
        # 流是空的：要么从没有过事件，要么全过期了。
        # 无法区分，保守判定为需要全量同步。
        return False
    return parse_id(entry_id) >= parse_id(str(rows[0][0]))
