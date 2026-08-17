"""任务模块。对外只暴露 service。

注意：这里不导出 router。包的 __init__ 一旦 import router，就会把 router
依赖的 auth.deps 等一并拉进模块初始化链，任何 service 层的跨模块调用
都会撞循环导入。main.py 直接从 <module>.router 导入即可。
"""

from apps.api.modules.task.service import (
    begin_execution,
    cancel_task,
    create_task,
    finish_execution,
    get_task,
    list_tasks,
    report_progress,
    retry_task,
)

__all__ = [
    "begin_execution",
    "cancel_task",
    "create_task",
    "finish_execution",
    "get_task",
    "list_tasks",
    "report_progress",
    "retry_task",
]
