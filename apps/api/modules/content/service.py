"""内容编辑模块的唯一对外入口。

这条写路径回答的是一个此前**根本没有答案**的问题：用户想把角色的发色
从"黑"改成"栗棕"，怎么改？在这之前唯一的写法是 `POST /revise`——
写一句话，让同一个 Agent 把整块产出重写一遍。那条路慢、要花钱、
而且模型可能顺手改坏别的字段（`agent/revise.py` 里 `diff_fields` 存在的
全部理由就是抓这件事）。做表格式的编辑界面而不做这条路，
得到的只会是一个"看起来能编辑、点了却在调模型"的假界面。

四条设计约束，改动前先读明白：

1. **不挑战 ADR-008。** 唯一权威仍是 `projects.current_state_json`，
   这里只是给它开了一条确定性的写路径，没有新建第二份产出真相。
2. **不建 AgentRun、不调模型、不产生一分钱流水。** 计费模块在这条链路上
   一行都不执行。`tests/integration/test_content_patch.py` 每个用例都在
   断言反面（无 Task、无 AgentRun、无 Ledger 流水、余额不动），
   因为唯一的风险就是哪天有人把它接回 `create_task`。
3. **一次 UI 操作 = 一个 batch = 一个事务。** 改产出、记变更、同步一致性
   投影三件事同生共死。半批记录撤销回去就是把产出改成一个谁也没要过的
   中间态。
4. **撤销是反向重放，不是删记录。** 撤销自己也是一批变更，因此可审计、
   可再被撤销（相当于重做），且不需要第二套写代码。
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.errors import AppError
from apps.api.core.logging import get_logger
from apps.api.modules.agent import service as agent_service
from apps.api.modules.consistency import service as consistency
from apps.api.modules.content import patching
from apps.api.modules.content import repository as repo
from apps.api.modules.content.models import ContentRevision
from apps.api.modules.project import service as project_service

log = get_logger(__name__)

MAX_HISTORY_BATCHES = 100


@dataclass(frozen=True, slots=True)
class PatchOutcome:
    batch_id: uuid.UUID
    role: str
    changed: int
    output: dict[str, Any]
    stale_roles: list[str]


@dataclass(frozen=True, slots=True)
class RevisionBatch:
    batch_id: uuid.UUID
    role: str
    source: str
    reason: str | None
    actor_user_id: uuid.UUID | None
    created_at: datetime
    rows: list[ContentRevision]
    undone_by_batch_id: uuid.UUID | None
    undoes_batch_id: uuid.UUID | None


# ---------------------------------------------------------------- 写


async def patch_output(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    role: str,
    patches: list[tuple[str, Any]],
    reason: str | None,
    actor_user_id: uuid.UUID,
) -> PatchOutcome:
    """按字段改一个阶段的产出。一次调用一个批次，一个事务。

    `patches` 是 `(JSON Pointer, 新值)` 的列表，顺序无关——重叠路径在
    `_plan` 里就被拒了，所以哪一条先写不影响结果。
    """
    schema = agent_service.output_schema_for(role)
    project = await project_service.get_project(db, org_id=org_id, project_id=project_id)
    state: dict[str, Any] = dict(project.current_state_json or {})
    block = _block_of(state, role)

    plan = _plan(schema, block, patches)
    if not plan.items:
        # 全是"改成原值"。不静默成功：前端会显示"已保存"，而历史里
        # 什么都查不到，用户下次找不到这次操作会以为记录丢了。
        raise AppError("common.validation_failed", message="本次提交没有任何实际改动")

    return await _commit_batch(
        db,
        org_id=org_id,
        project_id=project_id,
        role=role,
        state=state,
        block=plan.block,
        rows_spec=[(c.path, c.old, c.new) for c in plan.items],
        reason=reason,
        actor_user_id=actor_user_id,
        source="user_edit",
        undone_batch_id=None,
    )


async def undo_batch(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    batch_id: uuid.UUID,
    reason: str | None,
    actor_user_id: uuid.UUID,
) -> PatchOutcome:
    """把一批改动原样退回去。

    **只有当前值仍等于当时写下的新值，这一条才安全可撤。** 否则说明这个
    字段在这批之后又被改过，退回去等于把后来那次改动无声地抹掉——
    用户会看到一个自己从没做过的值，且找不到是谁干的。所以这种情况
    整批 409，并把冲突的字段告诉前端，让用户自己决定。

    不做"只撤能撤的那几条"：部分撤销出来的是一个既不是改前也不是改后的
    第三种状态，没有人要过它。
    """
    rows = await repo.batch_rows(db, org_id=org_id, project_id=project_id, batch_id=batch_id)
    if not rows:
        # 不存在、跨租户、或已软删——一律 404，不区分。
        # 区分就等于确认了"这个 batch_id 在别的租户下存在"。
        raise AppError("common.not_found", message=f"revision batch {batch_id}")

    undone = await repo.undo_batches_of(
        db, org_id=org_id, project_id=project_id, batch_ids=[batch_id]
    )
    if batch_id in undone:
        raise AppError(
            "common.conflict",
            message="这一批已经撤销过了",
            detail={"undone_by_batch_id": str(undone[batch_id])},
        )

    role = rows[0].role
    schema = agent_service.output_schema_for(role)
    project = await project_service.get_project(db, org_id=org_id, project_id=project_id)
    state: dict[str, Any] = dict(project.current_state_json or {})
    block = _block_of(state, role)

    # 反向重放：后写的先退。同一批内路径互不重叠（写入时已保证），
    # 顺序其实无关；照相反顺序走是为了让日志和将来支持重叠路径时都成立。
    work = copy.deepcopy(block)
    conflicts: list[str] = []
    plan: list[tuple[str, tuple[str, ...], Any, Any]] = []
    for row in reversed(rows):
        try:
            tokens = patching.parse_pointer(row.field_path)
            current = copy.deepcopy(patching.read_at(work, tokens))
        except patching.PathError:
            # 路径本身没了（比如整块被 revise 重写成了另一个形状）。
            # 这与"值被改过"是同一类问题，同样不能硬撤。
            conflicts.append(row.field_path)
            continue
        if current != row.new_value:
            conflicts.append(row.field_path)
            continue
        plan.append((row.field_path, tokens, current, row.old_value))

    if conflicts:
        raise AppError(
            "common.conflict",
            message="这些字段在这批改动之后又被改过，不能直接撤销",
            detail={"conflicts": conflicts[:20]},
        )

    for _, tokens, _, target in plan:
        patching.write_at(work, tokens, target)

    normalized = _validate(schema, work)
    rows_spec: list[tuple[str, Any, Any]] = []
    for path, tokens, current, _ in plan:
        value = _normalized_at(normalized, tokens, work)
        patching.write_at(work, tokens, value)
        rows_spec.append((path, current, value))

    return await _commit_batch(
        db,
        org_id=org_id,
        project_id=project_id,
        role=role,
        state=state,
        block=work,
        rows_spec=rows_spec,
        reason=reason,
        actor_user_id=actor_user_id,
        source="undo",
        undone_batch_id=batch_id,
    )


# ---------------------------------------------------------------- 读


async def list_revisions(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    limit: int = 20,
    cursor: datetime | None = None,
    role: str | None = None,
) -> tuple[list[RevisionBatch], datetime | None]:
    """变更历史，按批分组、按时间倒序。

    项目先查一次（不是多余的）：查不到就该 404，而不是返回一个空历史——
    空历史看起来像"这个项目没改过"，会把跨租户访问伪装成正常结果。
    """
    await project_service.get_project(db, org_id=org_id, project_id=project_id)

    limit = max(1, min(limit, MAX_HISTORY_BATCHES))
    heads = await repo.batch_heads(
        db, org_id=org_id, project_id=project_id, limit=limit + 1, cursor=cursor, role=role
    )
    has_more = len(heads) > limit
    page = heads[:limit]
    ids = [b for b, _ in page]

    rows = await repo.rows_of_batches(db, org_id=org_id, project_id=project_id, batch_ids=ids)
    undone = await repo.undo_batches_of(db, org_id=org_id, project_id=project_id, batch_ids=ids)

    grouped: dict[uuid.UUID, list[ContentRevision]] = {b: [] for b in ids}
    for row in rows:
        grouped[row.batch_id].append(row)

    items: list[RevisionBatch] = []
    for batch_id, at in page:
        members = grouped[batch_id]
        if not members:
            continue
        head = members[0]
        items.append(
            RevisionBatch(
                batch_id=batch_id,
                role=head.role,
                source=head.source,
                reason=head.reason,
                actor_user_id=head.actor_user_id,
                created_at=at,
                rows=members,
                undone_by_batch_id=undone.get(batch_id),
                undoes_batch_id=head.undone_batch_id,
            )
        )

    next_cursor = items[-1].created_at if has_more and items else None
    return items, next_cursor


# ---------------------------------------------------------------- 内部


@dataclass(frozen=True, slots=True)
class _Change:
    path: str
    tokens: tuple[str, ...]
    old: Any
    new: Any


@dataclass(frozen=True, slots=True)
class _Plan:
    block: dict[str, Any]
    items: list[_Change]


def _block_of(state: dict[str, Any], role: str) -> dict[str, Any]:
    block = state.get(role)
    if not isinstance(block, dict) or not block:
        raise AppError("common.not_found", message=f"还没有 {role} 的产出，先生成一版再改")
    return block


def _plan(schema: type[BaseModel], block: dict[str, Any], patches: list[tuple[str, Any]]) -> _Plan:
    """把请求里的 patch 列表变成"改完之后的整块 + 逐条改动"。

    全程在内存里的副本上做，一个字节都还没写库——校验失败时事务里
    干干净净，不需要靠 rollback 兜底（靠 rollback 兜底的写法在
    `rollback()` 之后读 ORM 属性会抛 MissingGreenlet，把 422 变成 500）。
    """
    parsed: list[tuple[str, tuple[str, ...], Any]] = []
    seen: set[tuple[str, ...]] = set()
    for path, value in patches:
        try:
            tokens = patching.parse_pointer(path)
            patching.check_value(value)
        except patching.PathError as exc:
            raise AppError(
                "common.validation_failed", message=str(exc), detail={"path": path}
            ) from exc
        # 同一条路径出现两次，或两条路径前缀重叠（同时改 /characters/0 和
        # /characters/0/name）都要拒：结果取决于哪条先写，而撤销时
        # "当时的旧值"也说不清是哪一份。
        for other in seen:
            if _overlaps(tokens, other):
                raise AppError(
                    "common.validation_failed",
                    message=f"{path} 与同一批里的另一条路径重复或重叠",
                    detail={"path": path},
                )
        seen.add(tokens)
        parsed.append((path, tokens, value))

    work = copy.deepcopy(block)
    olds: list[Any] = []
    for path, tokens, value in parsed:
        try:
            olds.append(copy.deepcopy(patching.read_at(work, tokens)))
            patching.write_at(work, tokens, value)
        except patching.PathError as exc:
            raise AppError(
                "common.validation_failed", message=str(exc), detail={"path": path}
            ) from exc

    normalized = _validate(schema, work)

    items: list[_Change] = []
    for (path, tokens, _), old in zip(parsed, olds, strict=True):
        # 只把**被改的那些路径**换成规范化后的值，不整块回写 normalized：
        # 整块回写会把 schema 演进补出来的默认值写进用户没碰过的字段，
        # 而那些改动没有对应的变更记录，撤销时补不回去。
        value = _normalized_at(normalized, tokens, work)
        patching.write_at(work, tokens, value)
        if old != value:
            items.append(_Change(path=path, tokens=tokens, old=old, new=value))

    return _Plan(block=work, items=items)


def _overlaps(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return long[: len(short)] == short


def _validate(schema: type[BaseModel], block: dict[str, Any]) -> dict[str, Any]:
    try:
        return patching.validate_block(schema, block)
    except patching.SchemaError as exc:
        raise AppError(
            "common.validation_failed", message=str(exc), detail={"errors": exc.errors}
        ) from exc


def _normalized_at(normalized: dict[str, Any], tokens: tuple[str, ...], fallback: Any) -> Any:
    """取规范化之后这个路径上的值；取不到就退回原样写入的值。

    取不到是可能的：pydantic 允许别名和 `exclude` 之类的形状变化，
    路径在 dump 之后未必还在原位。这时候宁可存用户写的原值——
    存一个查不到的值会让撤销写回一个不存在的东西。
    """
    try:
        return copy.deepcopy(patching.read_at(normalized, tokens))
    except patching.PathError:
        return copy.deepcopy(patching.read_at(fallback, tokens))


async def _commit_batch(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    role: str,
    state: dict[str, Any],
    block: dict[str, Any],
    rows_spec: list[tuple[str, Any, Any]],
    reason: str | None,
    actor_user_id: uuid.UUID,
    source: str,
    undone_batch_id: uuid.UUID | None,
) -> PatchOutcome:
    """把一批改动落库：产出、变更记录、过期记账、一致性投影，一个事务。"""
    batch_id = uuid.uuid4()

    state[role] = block
    stale = agent_service.mark_role_edited(state, role)
    await project_service.update_current_state(
        db, org_id=org_id, project_id=project_id, state=state
    )

    await repo.add_all(
        db,
        [
            repo.stage(
                org_id=org_id,
                project_id=project_id,
                batch_id=batch_id,
                role=role,
                field_path=path,
                old_value=old,
                new_value=new,
                reason=reason,
                actor_user_id=actor_user_id,
                source=source,
                undone_batch_id=undone_batch_id,
            )
            for path, old, new in rows_spec
        ],
    )

    await _sync_projection(db, org_id=org_id, project_id=project_id, role=role, block=block)
    await db.commit()

    log.info(
        "content.patched",
        project_id=str(project_id),
        role=role,
        batch_id=str(batch_id),
        source=source,
        changed=len(rows_spec),
    )
    return PatchOutcome(
        batch_id=batch_id,
        role=role,
        changed=len(rows_spec),
        output=block,
        stale_roles=stale,
    )


async def _sync_projection(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    role: str,
    block: dict[str, Any],
) -> None:
    """把改动同步进一致性引擎的角色/场景投影。

    **失败要让整个事务回滚**，这一点与编排器里的 `_sync_consistency`
    刻意不同。那边吞掉异常是因为它前面刚花过一次真实的模型调用，
    为一次写库失败让用户重跑一遍等于再花一次钱；这边这一批改动
    一分钱没花，重试是免费的，所以宁可整批失败也不能留下
    "产出改了、投影还是旧的"——那正是用户眼里"改了发色但出图还是黑发"
    的成因（评估文档 §5.1 风险 1）。

    冻结的角色/场景仍然不覆盖，那是一致性引擎自己的规则，这里不绕过它。
    """
    if role == "characters":
        await consistency.sync_from_characters_output(
            db, org_id=org_id, project_id=project_id, output=block
        )
    elif role == "scenes":
        await consistency.sync_from_scenes_output(
            db, org_id=org_id, project_id=project_id, output=block
        )
