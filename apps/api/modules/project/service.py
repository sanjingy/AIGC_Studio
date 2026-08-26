"""项目模块的唯一对外入口。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.errors import AppError
from apps.api.modules.gateway import catalog
from apps.api.modules.project import repository as repo
from apps.api.modules.project.models import Project

MAX_PAGE_SIZE = 100


async def create_project(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    title: str,
    route_type: str | None = None,
    budget_cap_credits: int | None = None,
) -> Project:
    row = await repo.create(
        db,
        org_id=org_id,
        owner_user_id=owner_user_id,
        title=title,
        route_type=route_type,
        budget_cap_credits=budget_cap_credits,
    )
    await db.commit()
    return row


async def get_project(db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID) -> Project:
    """取不到就 404。

    注意这里对"不存在"和"属于别的租户"返回同样的 404，
    不返回 403——403 等于确认了这个 ID 存在，是一个可枚举的信息泄露。
    """
    row = await repo.get(db, org_id=org_id, project_id=project_id)
    if row is None:
        raise AppError("common.not_found", message=f"project {project_id}")
    return row


async def list_projects(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    limit: int = 20,
    cursor: datetime | None = None,
    status: str | None = None,
) -> tuple[list[Project], datetime | None]:
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    # 多取一条用来判断还有没有下一页，避免额外的 count 查询
    rows = await repo.list_page(db, org_id=org_id, limit=limit + 1, cursor=cursor, status=status)
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = page[-1].created_at if has_more and page else None
    return page, next_cursor


async def update_project(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    title: str | None = None,
    route_type: str | None = None,
    budget_cap_credits: int | None = None,
) -> Project:
    row = await get_project(db, org_id=org_id, project_id=project_id)
    if title is not None:
        row.title = title.strip()
    if route_type is not None:
        row.route_type = route_type
    if budget_cap_credits is not None:
        row.budget_cap_credits = budget_cap_credits
    await db.commit()
    # UPDATE 之后必须 refresh：`updated_at` 是 server 端的 `onupdate=now()`，
    # SQLAlchemy 拿不到新值只能把它标成过期。`expire_on_commit=False` 管不着
    # 这一类列——读它会触发一次同步 refresh，在 async 上下文里直接抛
    # MissingGreenlet，把一次成功的更新变成 500。
    await db.refresh(row)
    return row


async def delete_project(db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID) -> None:
    row = await get_project(db, org_id=org_id, project_id=project_id)
    await repo.soft_delete(db, row)
    await db.commit()


async def project_exists(db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID) -> bool:
    return await repo.get(db, org_id=org_id, project_id=project_id) is not None


async def set_model_preference(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    capability: str,
    model_id: str | None,
) -> Project:
    """按能力设置项目级模型覆盖（ADR-024）。`model_id=None` 清掉这一条。

    两件事在这里定死：

    1. **按 key 合并，不整个覆盖。** 用户改了图片模型不该把之前存的
       文字模型偏好一起冲掉——前端只会传它正在改的那一个能力。
    2. **只接受目录里真实存在的 id。** 目录是"有哪些模型"的唯一真相源
       （`gateway/catalog.py`），存一个路由表里没有的 id 等于让偏好
       静默失效：Gateway 找不到匹配的路由就按默认优先级走，用户看着
       设置页显示"已选高质档"，实际跑的是另一个模型。

    **这里不碰任何计费。** ADR-024 硬约束 1（换模型必须重算 Credits 预估）
    约束的是"发起一次生成"那条路径——预估在 `billing/pricing.py` 按
    `model_pricing` 表算，而它读的是 Gateway 当次实际解析出来的模型。
    存偏好只是改了下次解析的输入，不预扣、不结算、不产生任何 Ledger 流水，
    因此不存在"绕过 model_pricing"的可能。
    """
    row = await get_project(db, org_id=org_id, project_id=project_id)

    if model_id is not None:
        allowed = catalog.model_ids(capability)
        if not allowed:
            raise AppError(
                "provider.params.invalid",
                message=f"能力 {capability!r} 还没有接入任何 Provider，无法选择模型",
                detail={"capability": capability},
            )
        if model_id not in allowed:
            raise AppError(
                "provider.params.invalid",
                message=f"模型 {model_id!r} 不在 {capability} 的可选范围内",
                detail={"capability": capability, "allowed": list(allowed)},
            )

    # JSONB 字段要**整个换成新 dict** 才会被 SQLAlchemy 标记为脏。
    # 原地 `row.model_preference[k] = v` 改的是同一个对象，flush 时
    # 比较不出差异，改动会被悄悄丢掉。
    merged = dict(row.model_preference or {})
    if model_id is None:
        merged.pop(capability, None)
    else:
        merged[capability] = model_id
    row.model_preference = merged

    await db.commit()
    # UPDATE 之后必须 refresh：`updated_at` 是 server 端的 `onupdate=now()`，
    # SQLAlchemy 拿不到新值只能把它标成过期。`expire_on_commit=False` 管不着
    # 这一类列——读它会触发一次同步 refresh，在 async 上下文里直接抛
    # MissingGreenlet，把一次成功的更新变成 500。
    await db.refresh(row)
    return row


async def get_model_preference(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, capability: str
) -> str | None:
    """读某个能力的项目级模型覆盖。查不到项目就当没有偏好。

    不抛 404：调用它的是 Gateway，此刻一次生成已经在跑了。项目对不上
    （被删、跨租户）应该让这次调用按默认路由继续，而不是把一次已经
    预扣过的生成变成 404——真正的租户校验早在建任务那一步做过了。
    """
    row = await repo.get(db, org_id=org_id, project_id=project_id)
    if row is None:
        return None
    value = (row.model_preference or {}).get(capability)
    return value if isinstance(value, str) and value else None
