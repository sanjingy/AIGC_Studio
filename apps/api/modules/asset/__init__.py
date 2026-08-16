"""资产模块。对外只暴露 service 与 router。"""

from apps.api.modules.asset.router import router
from apps.api.modules.asset.service import (
    complete_upload,
    create_upload,
    delete_asset,
    download_url,
    get_asset,
    list_assets,
    purge_abandoned_uploads,
)

__all__ = [
    "complete_upload",
    "create_upload",
    "delete_asset",
    "download_url",
    "get_asset",
    "list_assets",
    "purge_abandoned_uploads",
    "router",
]
