"""运维任务。"""

from __future__ import annotations

from typing import Any

from apps.api.core.db import session_scope
from apps.api.core.logging import get_logger
from apps.api.modules.asset import service as asset_service

log = get_logger(__name__)


async def purge_abandoned_uploads(ctx: dict[Any, Any], *_args: Any, **_kwargs: Any) -> int:
    """回收超时未完成的上传。

    参数名必须叫 `ctx`：arq 的 WorkerCoroutine 协议按参数名匹配，
    叫 `_ctx` 会导致 cron() 的类型检查不通过。

    没有这个任务，每一次用户点了上传又关掉页面，都会在库里留一条 pending
    记录和一个可能已经传了一半的对象。跑几个月就是一桶垃圾和一堆假数据。
    """
    del ctx
    async with session_scope() as db:
        count = await asset_service.purge_abandoned_uploads(db)
    return count
