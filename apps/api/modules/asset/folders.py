"""资产库文件夹：用户自建的分类。

**为什么单独一个文件，而不是塞进 service.py。** 这里要验证"被归类的这个
东西真的属于你"，而角色/场景档案是 agent 模块的数据，验证它要调
`agent.service`。`asset/service.py` 被 Worker 直接 import
（`worker/jobs/generation.py`），把 Agent 编排挂到出图任务的 import 链上
迟早撞循环导入——`library.py` 顶部记录过同一个理由。这个文件只被 router
和 library 引用，不在 Worker 的 import 链上。

**归类不是权限。** 文件夹只决定"在哪个抽屉里"，谁能看见什么仍然由
org_id + owner_user_id 决定。所以每次归类都要重新验证归属，不能因为
"它已经在我的文件夹里"就认为它是我的。
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.errors import AppError
from apps.api.core.logging import get_logger
from apps.api.modules.agent import service as agent_service
from apps.api.modules.asset import repository as repo
from apps.api.modules.asset.library import PROFILE_AGENTS
from apps.api.modules.asset.models import FOLDER_ITEM_TYPES, AssetFolder
from apps.api.modules.project import service as project_service

log = get_logger(__name__)

MAX_NAME_CHARS = 60


MAX_NAME_CHARS = 60


def _normalize(name: str) -> str:
    name = " ".join(name.split())  # 折叠空白：" 参考  图 " 和 "参考 图" 是同一个名字
    if not name:
        raise AppError("common.validation_failed", message="文件夹名不能为空")
    if len(name) > MAX_NAME_CHARS:
        raise AppError("common.validation_failed", message=f"文件夹名最长 {MAX_NAME_CHARS} 字")
    return name


async def create_folder(
    db: AsyncSession, *, org_id: uuid.UUID, owner_user_id: uuid.UUID, name: str
) -> AssetFolder:
    name = _normalize(name)

    if await repo.count_folders(db, org_id=org_id, owner_user_id=owner_user_id) >= repo.MAX_FOLDERS:
        raise AppError(
            "common.validation_failed",
            message=f"文件夹最多 {repo.MAX_FOLDERS} 个",
        )
    if await _same_name(db, org_id=org_id, owner_user_id=owner_user_id, name=name):
        raise AppError("common.conflict", message=f"已经有一个叫「{name}」的文件夹")

    row = await repo.create_folder(db, org_id=org_id, owner_user_id=owner_user_id, name=name)
    await db.commit()
    return row


async def rename_folder(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    folder_id: uuid.UUID,
    name: str,
) -> AssetFolder:
    folder = await get_folder(db, org_id=org_id, owner_user_id=owner_user_id, folder_id=folder_id)
    name = _normalize(name)
    if name != folder.name and await _same_name(
        db, org_id=org_id, owner_user_id=owner_user_id, name=name
    ):
        raise AppError("common.conflict", message=f"已经有一个叫「{name}」的文件夹")
    folder.name = name
    await db.commit()
    return folder


async def delete_folder(
    db: AsyncSession, *, org_id: uuid.UUID, owner_user_id: uuid.UUID, folder_id: uuid.UUID
) -> None:
    """删文件夹。**里面的东西一件都不删**，只是回到未分类。

    用户点的是"删除文件夹"，不是"删除这些资产"。连带删真实文件不可逆，
    而这个操作在界面上看起来只是整理分类。
    """
    folder = await get_folder(db, org_id=org_id, owner_user_id=owner_user_id, folder_id=folder_id)
    await repo.delete_items_of_folder(db, org_id=org_id, folder_id=folder_id)
    await repo.soft_delete_folder(db, folder)
    await db.commit()


async def get_folder(
    db: AsyncSession, *, org_id: uuid.UUID, owner_user_id: uuid.UUID, folder_id: uuid.UUID
) -> AssetFolder:
    folder = await repo.get_folder(db, org_id=org_id, folder_id=folder_id)
    # 别人的文件夹一律 404，不 403——403 等于确认这个 ID 存在
    if folder is None or folder.owner_user_id != owner_user_id:
        raise AppError("common.not_found", message=f"folder {folder_id}")
    return folder


async def _same_name(
    db: AsyncSession, *, org_id: uuid.UUID, owner_user_id: uuid.UUID, name: str
) -> bool:
    existing = await repo.list_folders(db, org_id=org_id, owner_user_id=owner_user_id)
    return any(f.name == name for f in existing)


# ---------------------------------------------------------------- 归类


async def classify(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    item_type: str,
    item_id: uuid.UUID,
    folder_id: uuid.UUID | None,
) -> None:
    """把一个东西放进文件夹；`folder_id=None` 表示移出，回到未分类。"""
    if item_type not in FOLDER_ITEM_TYPES:
        raise AppError(
            "common.validation_failed",
            message=f"未知的条目类型 {item_type}",
            detail={"allowed": list(FOLDER_ITEM_TYPES)},
        )

    await _assert_owns_item(
        db, org_id=org_id, owner_user_id=owner_user_id, item_type=item_type, item_id=item_id
    )

    if folder_id is None:
        await repo.delete_folder_item(db, org_id=org_id, item_type=item_type, item_id=item_id)
        await db.commit()
        return

    await get_folder(db, org_id=org_id, owner_user_id=owner_user_id, folder_id=folder_id)
    await repo.put_folder_item(
        db,
        org_id=org_id,
        owner_user_id=owner_user_id,
        folder_id=folder_id,
        item_type=item_type,
        item_id=item_id,
    )
    await db.commit()


async def _assert_owns_item(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    item_type: str,
    item_id: uuid.UUID,
) -> None:
    """归类前先确认这个东西真的是你的。跨租户/跨用户一律 404。"""
    if item_type == "asset":
        asset = await repo.get(db, org_id=org_id, asset_id=item_id)
        if asset is None or asset.owner_user_id != owner_user_id:
            raise AppError("common.not_found", message=f"asset {item_id}")
        return

    if item_type == "character":
        entry = await repo.get_character_entry(db, org_id=org_id, entry_id=item_id)
        if entry is None or entry.owner_user_id != owner_user_id:
            raise AppError("common.not_found", message=f"character entry {item_id}")
        return

    # profile：item_id 是 agent_runs.id。这条数据属于 agent 模块，
    # 只能问它要（ADR-009），并且要顺着它的 project 反查归属人——
    # 同一个 org 里别人的项目产出不属于"我的资产库"。
    run = await agent_service.get_run(db, org_id=org_id, run_id=item_id)
    if run.agent_id not in PROFILE_AGENTS:
        raise AppError("common.not_found", message=f"agent run {item_id} 不是角色/场景档案")
    project = await project_service.get_project(db, org_id=org_id, project_id=run.project_id)
    if project.owner_user_id != owner_user_id:
        raise AppError("common.not_found", message=f"agent run {item_id}")
