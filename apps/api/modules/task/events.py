"""事件定义与发件箱写入。

事件类型清单见 20_Realtime.md 第 4 节。

一条铁律：**事件携带最终状态，不携带增量指令。**
推 `status: succeeded`，不推「请把状态改成成功」。
这样客户端收到重复事件时 UI 不会错乱——SSE 重连必然产生重复。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.modules.task.models import OutboxEvent

# 与 20_Realtime.md 第 4 节对齐
EVENT_TASK_CREATED = "task.created"
EVENT_TASK_STARTED = "task.started"
EVENT_TASK_PROGRESS = "task.progress"
EVENT_TASK_SUCCEEDED = "task.succeeded"
EVENT_TASK_FAILED = "task.failed"
EVENT_TASK_CANCELLED = "task.cancelled"


async def emit(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID | None,
    type: str,
    data: dict[str, Any],
) -> None:
    """写入发件箱。

    **必须在业务事务内调用，不要单独 commit。**
    这正是 outbox 的意义：状态变更与事件同生共死。
    """
    db.add(
        OutboxEvent(
            org_id=org_id,
            project_id=project_id,
            type=type,
            data_json=data,
        )
    )
