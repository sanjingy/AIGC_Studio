"""任务执行。

S4 只接 Mock Provider——先用假实现把状态机、进度推送、失败重试、
取消这些控制流跑通，再换真模型（13_CodexDevelopmentGuide.md §5 Mock First）。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from apps.api.core.db import session_scope
from apps.api.core.errors import AppError
from apps.api.core.logging import get_logger
from apps.api.modules.task import service as task_service

log = get_logger(__name__)


async def execute_task(ctx: dict[Any, Any], task_id: str, *_a: Any, **_kw: Any) -> str:
    """执行一个任务。

    这个函数只负责控制流，具体干活的逻辑按 type 分发。
    每一步状态变更都走 task_service，因为事件必须与状态同事务写入。
    """
    del ctx
    tid = uuid.UUID(task_id)

    async with session_scope() as db:
        task = await task_service.begin_execution(db, task_id=tid)
    if task is None:
        return "skipped"  # 已取消、已跑过、或不存在

    task_type, payload = task.type, dict(task.input_json)

    try:
        output = await _dispatch(task_type, payload, task_id=tid)
    except AppError as exc:
        async with session_scope() as db:
            await task_service.finish_execution(
                db,
                task_id=tid,
                status="failed",
                error_code=exc.code,
                error_detail=exc.message,
            )
        log.info("task.failed", task_id=task_id, code=exc.code)
        return "failed"
    except Exception as exc:
        # 未预期异常：统一收敛成 common.internal，不把栈透给用户
        log.exception("task.crashed", task_id=task_id, error=repr(exc))
        async with session_scope() as db:
            await task_service.finish_execution(
                db,
                task_id=tid,
                status="failed",
                error_code="common.internal",
                error_detail=repr(exc)[:500],
            )
        return "failed"

    async with session_scope() as db:
        await task_service.finish_execution(db, task_id=tid, status="succeeded", output=output)
    return "succeeded"


async def _dispatch(
    task_type: str, payload: dict[str, Any], *, task_id: uuid.UUID
) -> dict[str, Any]:
    if task_type == "mock.echo":
        return await _mock_echo(payload, task_id=task_id)
    if task_type == "mock.fail":
        return await _mock_fail(payload)
    raise AppError("provider.params.invalid", message=f"no handler for {task_type}")


async def _mock_echo(payload: dict[str, Any], *, task_id: uuid.UUID) -> dict[str, Any]:
    """假的生成任务：分几步推进度，最后回显输入。

    存在意义是验证"进度能实时推到前端"这条链路——
    真实的视频生成要跑几分钟，没有进度推送用户会以为卡死了。
    """
    steps = int(payload.get("steps", 4))
    delay = float(payload.get("step_delay", 0.15))

    for i in range(1, steps + 1):
        await asyncio.sleep(delay)
        async with session_scope() as db:
            await task_service.report_progress(
                db,
                task_id=task_id,
                percent=int(i * 100 / steps),
                stage=f"step {i}/{steps}",
            )
    return {"echo": payload.get("message", ""), "steps": steps}


async def _mock_fail(payload: dict[str, Any]) -> dict[str, Any]:
    """按指定错误码失败，用于验证失败路径与重试策略。"""
    code = str(payload.get("error_code", "provider.unavailable"))
    raise AppError(code, message=f"mock failure: {code}")
