"""项目模块的唯一对外入口。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.errors import AppError
from apps.api.modules.gateway import catalog
from apps.api.modules.project import repository as repo
from apps.api.modules.project.models import ADAPTATION_MODES as ADAPTATION_MODES
from apps.api.modules.project.models import Project, ProjectLockVariables

MAX_PAGE_SIZE = 100

# `ADAPTATION_MODES` 在本模块顶部 import 进来，因此 `project_service.ADAPTATION_MODES`
# 是它的公开出口。编排器渲染门① 的可选项时从这里取——`project.models` 在 ruff 的
# banned-api 名单上，跨模块只走 service 层。


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
    2. **只接受目录里真实存在的 id**，外加文本能力的一个固定值
       `provider.custom.text`：表示"这个项目的文本走组织的自定义端点"，
       模型随端点走。端点没配时它会在生成时明确报错（`provider.byok.rejected`），
       不会静默退回平台默认。这里不查端点在不在——那是 Gateway 的表，
       项目模块只认目录。 目录是"有哪些模型"的唯一真相源
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

    custom = model_id == catalog.CUSTOM_TEXT_PROVIDER_ID and catalog.supports_custom_endpoint(
        capability
    )
    if model_id is not None and not custom:
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


async def update_current_state(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, state: dict[str, Any]
) -> Project:
    """整份写回编排状态（ADR-008 的唯一权威）。**不 commit。**

    不 commit 是有意的：字段级编辑要把"改产出 + 记变更 + 同步一致性投影"
    放在同一个事务里，任何一步失败都不能留下一份改了产出却没记账的状态。
    编排器那条路径自己有 `_save` 负责提交，两边不共用一个提交点。

    JSONB **必须整个换成新 dict**：原地 `state[k] = v` 改的是同一个对象，
    flush 时比较不出差异，改动被悄悄丢掉，表现为"改了但没变"。
    """
    row = await get_project(db, org_id=org_id, project_id=project_id)
    row.current_state_json = dict(state)
    return row


# ---------------------------------------------------------------- 锁定变量
#
# 门① 一次锁定四件事（ADR-037 第 2 条），其中三件在 `project_lock_variables`
# 上（第四件情节目录本来就是 `current_state_json["plot_index"]` 的产出）。
#
# 这几个函数是编排器与出图链路读画风、时代背景、改编模式的**唯一入口**。
# 一致性引擎按 `style_key` 去 `style_catalog` 取三套描述词，编排器按
# `era` / `ethnicity` / `adaptation_mode` 拼提示词变量。


async def get_lock_variables(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> ProjectLockVariables | None:
    """读这个项目的锁定变量。没有就返回 None，**不代建**。

    不代建是有意的：建一行意味着"这个项目已经有画风了"，而在门① 之前
    它还没有。代建会让"从没问过用户"和"用户选了缺省画风"变成同一种状态。
    """
    return await repo.get_lock_variables(db, org_id=org_id, project_id=project_id)


async def ensure_lock_variables(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    detected: dict[str, str] | None = None,
    origin: str = "detected",
) -> ProjectLockVariables:
    """取锁定变量，没有就按系统判定的结果建一行。**不 commit。**

    `detected` 是系统按原文证据得出的时代背景判定（来自情节目录产出），
    用户在门① 可以改。只在**新建**时写进去：已经存在的行可能是用户
    确认过的，重跑一次情节目录不该把他的选择冲掉。

    `origin="migrated"` 供存量项目的运行时补齐用——迁移脚本已经补过一轮，
    但在迁移之后、部署之前建的项目仍会漏网，那些项目第一次被读到时
    在这里补上，并如实标成 migrated 而不是伪装成 detected。
    """
    existing = await repo.get_lock_variables(db, org_id=org_id, project_id=project_id)
    if existing is not None:
        return existing

    fields = detected or {}
    return await repo.create_lock_variables(
        db,
        org_id=org_id,
        project_id=project_id,
        origin=origin,
        era=str(fields.get("era", "")),
        region=str(fields.get("region", "")),
        ethnicity=str(fields.get("ethnicity", "")),
        era_evidence=str(fields.get("era_evidence", "")),
    )


async def set_lock_variables(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    style_key: str | None = None,
    era: str | None = None,
    region: str | None = None,
    ethnicity: str | None = None,
    adaptation_mode: str | None = None,
) -> ProjectLockVariables:
    """改锁定变量。只改传进来的那几项，None 表示"这次不动它"。

    两条校验在这里定死：

    1. **画风必须在目录里。** 存一个 `style_catalog` 里没有的 key，等于让
       选择静默失效——`ensure_style` 解析不到就会报错，而那时用户已经
       走到角色出图那一步了，错误出现的位置离原因很远。
    2. **画风档案一旦建出来就不能再改 key。** `ensure_style` 是
       "已存在就不覆盖"的，改了 key 也不会生效，界面却显示改成功了。
       宁可在这里明确拒绝，也不要一次看起来成功的无效操作。
       （真要换画风是"新建一个风格版本 + 已生成的镜头全部重出"，
       那是 ADR-033 候选版本的范围，不在这条路径上。）
    """
    row = await get_project(db, org_id=org_id, project_id=project_id)
    lock = await ensure_lock_variables(db, org_id=org_id, project_id=project_id)
    del row

    if style_key is not None:
        key = style_key.strip()
        # 延迟导入：consistency 那边也会反向调 project.service 读 style_key，
        # 模块顶层互相 import 会撞循环导入。
        from apps.api.modules.consistency import service as consistency

        if key and await consistency.get_style_entry(db, key=key) is None:
            raise AppError("provider.params.invalid", message=f"未知画风 {key}")
        if key != lock.style_key:
            frozen = await consistency.get_style(db, org_id=org_id, project_id=project_id)
            if frozen is not None:
                raise AppError(
                    "common.conflict",
                    message="这个项目的画风档案已经建立，改画风需要重出全部已生成的画面",
                    detail={"style_key": frozen.style_key or lock.style_key},
                )
        lock.style_key = key

    if era is not None:
        lock.era = era.strip()
    if region is not None:
        lock.region = region.strip()
    if ethnicity is not None:
        lock.ethnicity = ethnicity.strip()

    if adaptation_mode is not None:
        mode = adaptation_mode.strip()
        if mode not in ADAPTATION_MODES:
            raise AppError(
                "common.validation_failed",
                message=f"未知的改编模式 {mode!r}",
                detail={"allowed": list(ADAPTATION_MODES)},
            )
        lock.adaptation_mode = mode

    await db.commit()
    await db.refresh(lock)
    return lock


async def confirm_lock_variables(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    confirmed_by: uuid.UUID,
) -> ProjectLockVariables:
    """门① 通过：这四件事从此是用户确认过的。**不 commit**——

    调用它的是 `orchestrator.resolve_gate`，那一步还要写阶段推进，
    两件事必须在同一个事务里。分开提交会出现"门开着但变量已确认"
    或者反过来的中间态，而这两种状态在界面上都解释不清。

    幂等：重复确认只保留第一次的时间与人。审核本身已经有
    "同一条 approval 不能处理两次"的 409，这里的幂等是为了别的入口。
    """
    lock = await ensure_lock_variables(db, org_id=org_id, project_id=project_id)
    if lock.confirmed_at is None:
        lock.confirmed_at = datetime.now(UTC)
        lock.confirmed_by = confirmed_by
        lock.origin = "confirmed"
    return lock


async def confirm_anchors(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> ProjectLockVariables:
    """门③ 通过：空间锚点卡从此是用户一次性确认过的。**不 commit**，理由同上。

    与门① 分开记时间戳：两道门确认的是完全不同的东西，
    合成一个字段就分不清"画风确认过、锚点还没有"这种正常中间态。

    **走到这里还没有锁定变量行 = 这是个存量项目。** 新项目一定先过门①，
    而门① 打开时 `_plan_gate_summary` 就把行建出来了。所以这里代建的行
    只可能属于"ADR-037 上线时已经越过门① 位置"的那批项目——迁移应该已经
    给它们补过一行，漏网的（迁移之后、部署之前建的）在这里补上，
    `origin="migrated"` 如实标注**从来没有人看过一眼**，
    而不是伪装成 detected 让界面显示得像系统判定过。
    """
    lock = await ensure_lock_variables(db, org_id=org_id, project_id=project_id, origin="migrated")
    if lock.anchors_confirmed_at is None:
        lock.anchors_confirmed_at = datetime.now(UTC)
    return lock
