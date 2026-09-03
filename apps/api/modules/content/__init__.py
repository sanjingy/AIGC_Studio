"""内容编辑模块：字段级确定性编辑 + 变更历史 + 按批撤销（ADR-029）。对外只暴露 service。

注意：这里不导出 router。包的 __init__ 一旦 import router，就会把 router
依赖的 auth.deps 等一并拉进模块初始化链，任何 service 层的跨模块调用
都会撞循环导入。main.py 直接从 <module>.router 导入即可。
"""

from apps.api.modules.content.service import (
    list_revisions,
    patch_output,
    undo_batch,
)

__all__ = [
    "list_revisions",
    "patch_output",
    "undo_batch",
]
