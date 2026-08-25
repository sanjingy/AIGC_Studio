"""资产库聚合：把"用户历史生成过的东西"合成一个视图。

用户心里的"我的资产"是一件事，系统里却存在两个地方：

    assets 表          图片 / 视频这类**真占磁盘**的二进制文件
    agent_runs 表      角色档案 / 场景档案这类结构化文本产出

配额只算前者——后者是几 KB 的 JSON，把它计进 1 GiB 的容量里既没有
意义，也会让"清理素材腾空间"这个动作变得莫名其妙（删掉一个角色设定
并不会让你多存下一张图）。

**为什么单独一个文件而不是塞进 service.py。** 这里要同时调用
project.service 与 agent.service，而 service.py 被 Worker 直接 import
（`worker/jobs/generation.py`）。把这两个依赖加进 service.py，等于让
出图任务的 import 链上多挂一整个 Agent 编排模块，且一旦将来 agent 侧
需要 asset.service（M2 出图落库时很可能需要）就会撞循环导入。
这个文件只被 router 引用，不在 Worker 的 import 链上。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.modules.agent import service as agent_service
from apps.api.modules.agent.models import AgentRun
from apps.api.modules.asset import repository as repo
from apps.api.modules.asset import service as asset_service
from apps.api.modules.asset.models import Asset, CharacterEntry
from apps.api.modules.project import service as project_service

# 资产库要展示的结构化产出。键是 agent_id，值是给前端的分类名——
# 前端按它决定用 CharactersView 还是 ScenesView 渲染。
PROFILE_AGENTS: dict[str, str] = {
    "visual.character.v1": "characters",
    "visual.scene.v1": "scenes",
}

# 扫多少个项目。分页拉取，够覆盖任何真实用户；再多说明该做服务端聚合了。
MAX_PROJECTS = 500
_PROJECT_PAGE = 100


@dataclass(frozen=True, slots=True)
class ProfileEntry:
    """一个项目里某一类档案的最新一版。"""

    project_id: uuid.UUID
    project_title: str
    kind: str
    agent_id: str
    run_id: uuid.UUID
    output: dict[str, Any]
    created_at: datetime
    folder_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class FolderSummary:
    """侧边栏用的一个文件夹。条目数实时数，不缓存在行上——
    缓存的计数必然与关系表对不上，而它只是给人看的。"""

    id: uuid.UUID
    name: str
    item_count: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CharacterEntryView:
    """资产库里的独立角色档案（不挂项目）。"""

    entry: CharacterEntry
    folder_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class Library:
    usage: asset_service.QuotaUsage
    assets: list[Asset]
    next_cursor: datetime | None
    profiles: list[ProfileEntry]
    characters: list[CharacterEntryView]
    folders: list[FolderSummary]
    #: (类型, id) → 所在文件夹。前端据此给"全部"视图里的条目打标。
    folder_of: dict[tuple[str, uuid.UUID], uuid.UUID]


async def _owned_projects(
    db: AsyncSession, *, org_id: uuid.UUID, owner_user_id: uuid.UUID
) -> list[Any]:
    """本用户在本租户下的项目。

    走 project.service 而不是直接查 projects 表：跨模块只调对方 service
    是硬规则（ADR-009），而且 `list_projects` 已经处理了软删除与 org 作用域。
    """
    owned: list[Any] = []
    cursor: datetime | None = None
    while len(owned) < MAX_PROJECTS:
        page, cursor = await project_service.list_projects(
            db, org_id=org_id, limit=_PROJECT_PAGE, cursor=cursor
        )
        owned.extend(p for p in page if p.owner_user_id == owner_user_id)
        if cursor is None:
            break
    return owned[:MAX_PROJECTS]


async def get_library(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    asset_type: str | None = None,
    limit: int = 40,
    cursor: datetime | None = None,
    folder_id: uuid.UUID | None = None,
) -> Library:
    """我的资产库。

    `folder_id=None` 是"全部"视图：分页的资产 + 每个项目最新的档案，
    与加文件夹之前完全一致。给了 `folder_id` 就只列这个文件夹里的东西，
    此时不分页——文件夹的容量由 `repo.MAX_FOLDER_ITEMS` 封顶，
    先分页再按归类过滤会得到一页空结果，那比不分页糟得多。
    """
    usage = await asset_service.quota_usage(db, org_id=org_id, owner_user_id=owner_user_id)

    folders, folder_of = await folder_index(db, org_id=org_id, owner_user_id=owner_user_id)

    projects = await _owned_projects(db, org_id=org_id, owner_user_id=owner_user_id)
    titles = {p.id: p.title for p in projects}

    if folder_id is None:
        assets, next_cursor = await asset_service.list_assets(
            db,
            org_id=org_id,
            owner_user_id=owner_user_id,
            asset_type=asset_type,
            limit=limit,
            cursor=cursor,
        )
        runs = await agent_service.list_outputs_by_agent(
            db,
            org_id=org_id,
            project_ids=list(titles),
            agent_ids=list(PROFILE_AGENTS),
        )
        runs = _latest_per_project(runs)
        entries = await repo.list_character_entries(
            db, org_id=org_id, owner_user_id=owner_user_id, limit=repo.MAX_CHARACTER_ENTRIES
        )
    else:
        picked = await repo.list_folder_items(
            db, org_id=org_id, owner_user_id=owner_user_id, folder_id=folder_id
        )
        ids: dict[str, list[uuid.UUID]] = {}
        for item in picked:
            ids.setdefault(item.item_type, []).append(item.item_id)

        assets = await repo.list_assets_by_ids(db, org_id=org_id, asset_ids=ids.get("asset", []))
        if asset_type:
            assets = [a for a in assets if a.type == asset_type]
        next_cursor = None
        # 按 id 取，不走"每个项目最新一版"——用户归类的可能正是某个旧版本，
        # 用最新一版去过滤会让它在自己的文件夹里凭空消失。
        runs = await agent_service.list_runs_by_ids(
            db, org_id=org_id, run_ids=ids.get("profile", [])
        )
        entries = await repo.list_character_entries_by_ids(
            db, org_id=org_id, entry_ids=ids.get("character", [])
        )

    profiles = [
        ProfileEntry(
            project_id=run.project_id,
            project_title=titles[run.project_id],
            kind=PROFILE_AGENTS[run.agent_id],
            agent_id=run.agent_id,
            run_id=run.id,
            output=run.output_json,
            created_at=run.created_at,
            folder_id=folder_of.get(("profile", run.id)),
        )
        # 项目已删（或不属于本人）的产出不该出现在"我的资产库"里。
        # 全部视图里 titles 就是查询条件，这里是文件夹视图的兜底。
        for run in runs
        if run.output_json is not None and run.project_id in titles
    ]

    characters = [
        CharacterEntryView(entry=e, folder_id=folder_of.get(("character", e.id))) for e in entries
    ]

    return Library(
        usage=usage,
        assets=assets,
        next_cursor=next_cursor,
        profiles=profiles,
        characters=characters,
        folders=folders,
        folder_of=folder_of,
    )


def _latest_per_project(runs: list[AgentRun]) -> list[AgentRun]:
    """只留每个 (项目, Agent) 的最新一版。runs 已按时间倒序，先到的就是最新的。

    不这么做的话，改过三次角色的项目会在资产库里出现三份角色档案，
    用户分不清哪份是当前生效的——而当前生效的只有最新那份。
    """
    seen: set[tuple[uuid.UUID, str]] = set()
    out: list[AgentRun] = []
    for run in runs:
        key = (run.project_id, run.agent_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(run)
    return out


async def folder_index(
    db: AsyncSession, *, org_id: uuid.UUID, owner_user_id: uuid.UUID
) -> tuple[list[FolderSummary], dict[tuple[str, uuid.UUID], uuid.UUID]]:
    """侧边栏的文件夹列表 + (类型, id) → 文件夹 的对照表。

    一次把归类关系全取回来，在内存里数数和查表：列表里每条各查一次
    就是 N+1，而关系行数已被 `repo.MAX_FOLDER_ITEMS` 封顶。
    """
    rows = await repo.list_folders(db, org_id=org_id, owner_user_id=owner_user_id)
    items = await repo.list_folder_items(db, org_id=org_id, owner_user_id=owner_user_id)

    counts: dict[uuid.UUID, int] = {}
    folder_of: dict[tuple[str, uuid.UUID], uuid.UUID] = {}
    for item in items:
        counts[item.folder_id] = counts.get(item.folder_id, 0) + 1
        folder_of[(item.item_type, item.item_id)] = item.folder_id

    folders = [
        FolderSummary(id=f.id, name=f.name, item_count=counts.get(f.id, 0), created_at=f.created_at)
        for f in rows
    ]
    return folders, folder_of
