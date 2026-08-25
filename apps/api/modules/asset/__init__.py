"""资产模块。对外只暴露 service。

注意：这里不导出 router。包的 __init__ 一旦 import router，就会把 router
依赖的 auth.deps 等一并拉进模块初始化链，任何 service 层的跨模块调用
都会撞循环导入。main.py 直接从 <module>.router 导入即可。
"""

from apps.api.modules.asset.service import (
    QuotaUsage,
    complete_upload,
    create_upload,
    delete_asset,
    download_url,
    ensure_quota,
    get_asset,
    list_assets,
    purge_abandoned_uploads,
    quota_usage,
    register_generated,
)

__all__ = [
    "QuotaUsage",
    "complete_upload",
    "create_upload",
    "delete_asset",
    "download_url",
    "ensure_quota",
    "get_asset",
    "list_assets",
    "purge_abandoned_uploads",
    "quota_usage",
    "register_generated",
]
