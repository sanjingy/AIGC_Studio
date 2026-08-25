"""从产品界面触发一次真实出图。

在这之前，出图能力是"有后端无 UI"：`worker/jobs/generation.py` 能出图，
`compose.py` 能合成提示词，但**没有任何一条路径把两者接起来**——
唯一跑通过的是 `scripts/validation_slice.py`，它绕开了 UI、任务队列和
计费。这个模块就是那条缺失的路径。

三条规则决定了这里的所有设计：

1. **提示词只能由 `compose` 合成。** 前端传过来的任何画面描述都不作数，
   风格词永远由系统注入（17_ConsistencyEngine.md §4）。所以这两个端点
   的入参只有"要画哪个角色 / 哪一镜"，没有 prompt。
2. **走同一条任务路径。** 建任务、预扣、入队、结算全部交给
   `task.service.create_task`，这里一行状态机逻辑都不写——
   执行状态的唯一真相是 `tasks.status`（ADR-008）。
3. **前置条件缺失要在花钱之前拦下来。** 没有角色档案就没有风格档案，
   合成不出提示词。这时候返回 `consistency.profile.missing`，
   而不是让用户拿到一个跑到 Worker 才炸的 500。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.errors import AppError
from apps.api.core.logging import get_logger
from apps.api.modules.consistency import compose
from apps.api.modules.consistency import service as consistency
from apps.api.modules.consistency.models import CharacterProfile, StyleProfile
from apps.api.modules.project import service as project_service
from apps.api.modules.task import service as task_service
from apps.api.modules.task.models import Task

log = get_logger(__name__)

IMAGE_TASK = "image.generate"

# 出图尺寸。写死在这里不违反"价格不进代码"——它是画面规格不是价格，
# 计费按张数算（pricing._shape 只看 n）。
DEFAULT_SIZE = "1024*1024"

SUBJECT_CHARACTER = "character"
SUBJECT_SHOT = "shot"

# 一次列多少条出图记录。26 个镜号 + 若干角色立绘，再加上重试，
# 100 条足够覆盖一个项目的全部出图，且不用分页。
MAX_RENDERS = 100


@dataclass(frozen=True, slots=True)
class Render:
    """一次出图的对外视图：谁、跑到哪一步、图在哪。

    它不是新的一份状态，而是 `tasks` 那一行的投影——
    状态永远从 `tasks.status` 读（ADR-008），这里只负责把
    "这个任务画的是哪个角色/哪一镜"和"产出的资产 id"翻译出来。
    """

    task_id: uuid.UUID
    subject_kind: str
    subject_ref: str | None
    shot_index: int | None
    status: str
    progress: int
    error_code: str | None
    asset_id: uuid.UUID | None
    created_at: datetime


async def _project_state(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> tuple[Any, dict[str, Any]]:
    """取项目和它的状态。跨租户在这里就 404 了，不是 403。"""
    project = await project_service.get_project(db, org_id=org_id, project_id=project_id)
    return project, dict(project.current_state_json or {})


async def _profiles(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, state: dict[str, Any]
) -> tuple[StyleProfile, list[CharacterProfile]]:
    """拿到这个项目的风格档案和角色资产包，必要时按当前 state 补齐。

    补齐这一步不是多余的：编排器接入一致性引擎是这次才加的，在那之前
    跑过角色阶段的项目在库里没有任何档案；聊天修订改过角色之后，
    `state` 里的外貌也比库里新。两个写入动作都幂等（风格已存在不覆盖、
    角色已冻结不覆盖），所以这里可以无条件调。
    """
    characters_output = state.get("characters")
    if not isinstance(characters_output, dict) or not characters_output.get("characters"):
        raise AppError(
            "consistency.profile.missing",
            message="项目还没有角色档案产出",
            detail={"missing_stage": "characters"},
        )

    style, profiles = await consistency.sync_from_characters_output(
        db, org_id=org_id, project_id=project_id, output=characters_output
    )
    await db.commit()

    if not profiles:
        raise AppError(
            "consistency.profile.missing",
            message="角色档案里没有可用的角色（缺 ref）",
            detail={"missing_stage": "characters"},
        )
    return style, profiles


async def _create(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project: Any,
    created_by: uuid.UUID,
    payload: dict[str, Any],
    idempotency_key: str | None,
) -> Task:
    """建出图任务。

    项目预算上限照 `POST /tasks` 那条路传，不另起一套——
    从这里进来的出图不该绕过 `budget_cap`。
    """
    task, _created = await task_service.create_task(
        db,
        org_id=org_id,
        project_id=project.id,
        created_by=created_by,
        task_type=IMAGE_TASK,
        input_json=payload,
        idempotency_key=idempotency_key,
        project_budget_cap=project.budget_cap_credits,
        project_spent=project.spent_credits,
    )
    return task


async def request_character_portrait(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    created_by: uuid.UUID,
    ref: str,
    idempotency_key: str | None = None,
) -> Task:
    """给一个角色出基准立绘。

    立绘是后续所有镜头的一致性基准，提示词由 `reference_portrait_prompt`
    合成——中性表情、中性光照、纯色背景，任何戏剧化的光影都会污染基准。
    """
    project, state = await _project_state(db, org_id=org_id, project_id=project_id)
    style, profiles = await _profiles(db, org_id=org_id, project_id=project_id, state=state)

    profile = next((p for p in profiles if p.ref == ref), None)
    if profile is None:
        # 角色不属于这个项目，和"项目不属于这个租户"一样只给 404
        raise AppError("common.not_found", message=f"character {ref}")

    payload: dict[str, Any] = {
        "prompt": compose.reference_portrait_prompt(profile, style),
        "negative_prompt": style.negative_tokens,
        "size": DEFAULT_SIZE,
        "n": 1,
        # 立绘用项目基准种子本身，镜头才在它上面按镜号偏移。
        # 同一个角色重出立绘落在同一个种子上，便于对比改动。
        "seed": style.seed_base or None,
        "project_id": str(project_id),
        "owner_user_id": str(created_by),
        "subject_kind": SUBJECT_CHARACTER,
        "subject_ref": profile.ref,
    }
    task = await _create(
        db,
        org_id=org_id,
        project=project,
        created_by=created_by,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    log.info(
        "consistency.portrait_requested",
        project_id=str(project_id),
        ref=profile.ref,
        task_id=str(task.id),
    )
    return task


def _find_shot(state: dict[str, Any], shot_index: int) -> dict[str, Any]:
    storyboard = state.get("storyboard")
    if not isinstance(storyboard, dict) or not storyboard.get("shots"):
        raise AppError(
            "consistency.profile.missing",
            message="项目还没有分镜产出",
            detail={"missing_stage": "storyboard"},
        )
    for shot in storyboard["shots"]:
        if isinstance(shot, dict) and int(shot.get("index", -1)) == shot_index:
            return shot
    raise AppError("common.not_found", message=f"shot {shot_index}")


async def request_shot_image(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    created_by: uuid.UUID,
    shot_index: int,
    idempotency_key: str | None = None,
) -> Task:
    """给一个镜号出图。

    出场人物按分镜表的 `character_refs` 取，顺序照抄——`compose_shot`
    对角色顺序敏感，顺序一变模型的注意力分布就变。分镜表里写了但
    档案里没有的 ref 直接跳过：那是 Agent 造了个不存在的角色，
    为它整镜失败不划算，而且缺谁在日志里看得见。
    """
    project, state = await _project_state(db, org_id=org_id, project_id=project_id)
    shot = _find_shot(state, shot_index)
    style, profiles = await _profiles(db, org_id=org_id, project_id=project_id, state=state)

    by_ref = {p.ref: p for p in profiles}
    refs = [str(r) for r in shot.get("character_refs", []) if isinstance(r, str)]
    characters = [by_ref[r] for r in dict.fromkeys(refs) if r in by_ref]
    if missing := [r for r in refs if r not in by_ref]:
        log.warning(
            "consistency.shot_unknown_refs",
            project_id=str(project_id),
            shot_index=shot_index,
            refs=missing,
        )

    composed = compose.compose_shot(
        content=str(shot.get("content", "")),
        style=style,
        characters=characters,
        shot_index=shot_index,
    )

    payload: dict[str, Any] = {
        "prompt": composed.prompt,
        "negative_prompt": composed.negative_prompt,
        "size": DEFAULT_SIZE,
        "n": 1,
        "seed": composed.seed,
        "project_id": str(project_id),
        "owner_user_id": str(created_by),
        "subject_kind": SUBJECT_SHOT,
        "shot_index": shot_index,
    }
    task = await _create(
        db,
        org_id=org_id,
        project=project,
        created_by=created_by,
        payload=payload,
        idempotency_key=idempotency_key,
    )

    # 条件化记录落在**建任务时**，不等任务跑完。
    #
    # 它回答的问题是"这一镜是拿什么去生成的"，而这个问题在**失败的时候
    # 最需要答案**——等跑完再落，崩掉的那些镜头恰好一条记录都没有，
    # 而它们才是要查的。提示词也确实是在这里定下来的：Worker 只是
    # 拿着 input_json 去调上游，不会再改一个字。
    #
    # 代价是 asset_id 留空（图还不存在），以及被取消的任务会留下一条
    # "打算这么生成但没跑"的记录。前者由 tasks.output_json 补上，
    # 后者是可接受的噪音——宁可多一条没跑的记录，也不要少一条崩掉的。
    #
    # attempt 用 task.attempt + 1：`begin_execution` 会把它自增，
    # "即将执行的那一次"永远是当前值 +1，与计费的预扣编号对齐。
    await consistency.record_conditioning(
        db,
        org_id=org_id,
        project_id=project_id,
        shot_index=shot_index,
        composed=composed,
        style_id=style.id,
        attempt=task.attempt + 1,
    )
    await db.commit()

    log.info(
        "consistency.shot_requested",
        project_id=str(project_id),
        shot_index=shot_index,
        characters=len(characters),
        task_id=str(task.id),
    )
    return task


def _asset_id_of(output: dict[str, Any] | None) -> uuid.UUID | None:
    """从任务产出里取第一张图的资产 id。

    出图任务一次只要一张（n=1），多张的情况留给未来的批量出图；
    取不到就当没有，绝不因为产出格式不合预期让整个列表 500。
    """
    ids = (output or {}).get("asset_ids")
    if not isinstance(ids, list) or not ids:
        return None
    try:
        return uuid.UUID(str(ids[0]))
    except ValueError:
        return None


async def list_renders(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> list[Render]:
    """列出这个项目的全部出图，最新的在前。

    刻意不做"每个角色/镜号只留最新一条"的收敛：那是展示策略，
    前端取第一条就是最新的，而保留全部让重试历史仍然可见。
    """
    await project_service.get_project(db, org_id=org_id, project_id=project_id)
    rows, _cursor = await task_service.list_tasks(
        db, org_id=org_id, project_id=project_id, limit=MAX_RENDERS
    )

    out: list[Render] = []
    for row in rows:
        if row.type != IMAGE_TASK:
            continue
        payload = dict(row.input_json or {})
        kind = str(payload.get("subject_kind", ""))
        if kind not in (SUBJECT_CHARACTER, SUBJECT_SHOT):
            # 从 /tasks 直接建的出图任务没有 subject 标记，
            # 它不属于任何角色或镜号，列出来只会让前端不知道往哪贴
            continue
        raw_index = payload.get("shot_index")
        out.append(
            Render(
                task_id=row.id,
                subject_kind=kind,
                subject_ref=str(payload["subject_ref"]) if payload.get("subject_ref") else None,
                shot_index=int(raw_index) if isinstance(raw_index, int) else None,
                status=row.status,
                progress=row.progress,
                error_code=row.error_code,
                asset_id=_asset_id_of(row.output_json),
                created_at=row.created_at,
            )
        )
    return out
