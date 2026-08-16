"""项目模块。对外只暴露 service 与 router。"""

from apps.api.modules.project.router import router
from apps.api.modules.project.service import (
    create_project,
    delete_project,
    get_project,
    list_projects,
    project_exists,
    update_project,
)

__all__ = [
    "create_project",
    "delete_project",
    "get_project",
    "list_projects",
    "project_exists",
    "router",
    "update_project",
]
