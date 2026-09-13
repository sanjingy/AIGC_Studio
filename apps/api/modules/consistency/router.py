"""出图触发端点。

挂在 `/projects/{project_id}/images` 下而不是 `/tasks`：这两个动作的
入参是"画哪个角色/哪一镜"，提示词由系统合成，和"随便建一个出图任务"
不是同一件事。放进项目子路径也让权限判断只有一条路——先取项目，
取不到就 404，角色和镜号都必须属于这个项目。

返回体直接用 `task.schemas.TaskOut`：出图就是一个普通任务，
前端已经认识这个形状。另造一份"出图任务"的 DTO 只会多一份要同步的真相。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Header, Path, status

from apps.api.modules.auth.deps import CurrentUser, DbSession
from apps.api.modules.consistency import render
from apps.api.modules.consistency.schemas import (
    BaseImageIn,
    BaseImageOut,
    RenderIn,
    RenderOut,
)
from apps.api.modules.task.schemas import TaskOut

router = APIRouter(prefix="/projects/{project_id}/images", tags=["images"])


@router.get("", response_model=list[RenderOut])
async def list_renders(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> list[RenderOut]:
    rows = await render.list_renders(db, org_id=user.org_id, project_id=project_id)
    return [RenderOut.model_validate(r) for r in rows]


@router.post("/characters/{ref}", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
async def generate_character_portrait(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    payload: RenderIn | None = Body(None),
    ref: str = Path(min_length=1, max_length=32),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> TaskOut:
    """给角色出基准立绘。真实出图调用，会扣 Credits。

    `source="local"` 时改由用户自己电脑上的 Codex 画（试点）。**选了本机
    就只走本机**：不可用会在这里就被拦下来（不建任务、不预扣），
    跑失败也不会悄悄改调付费 API。
    """
    task = await render.request_character_portrait(
        db,
        org_id=user.org_id,
        project_id=project_id,
        created_by=user.id,
        ref=ref,
        source=(payload.source if payload else render.IMAGE_SOURCE_API),
        idempotency_key=idempotency_key,
        prompt_run_id=(payload.prompt_run_id if payload else None),
        instruction=(payload.instruction if payload else ""),
    )
    return TaskOut.model_validate(task)


@router.post("/scenes/{ref}", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
async def generate_scene_reference(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    payload: RenderIn | None = Body(None),
    ref: str = Path(min_length=1, max_length=32),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> TaskOut:
    """给场景出基准参考图。真实出图调用，会扣 Credits。"""
    task = await render.request_scene_reference(
        db,
        org_id=user.org_id,
        project_id=project_id,
        created_by=user.id,
        ref=ref,
        source=(payload.source if payload else render.IMAGE_SOURCE_API),
        idempotency_key=idempotency_key,
        prompt_run_id=(payload.prompt_run_id if payload else None),
        instruction=(payload.instruction if payload else ""),
    )
    return TaskOut.model_validate(task)


@router.put("/characters/{ref}", response_model=BaseImageOut)
async def set_character_portrait(
    project_id: uuid.UUID,
    payload: BaseImageIn,
    user: CurrentUser,
    db: DbSession,
    ref: str = Path(min_length=1, max_length=32),
) -> BaseImageOut:
    """把一张已有资产钉成角色的基准立绘。**不出图，不扣 Credits。**

    PUT 而不是 POST，语义差别是实打实的：POST 到同一个路径是"再生成
    一张新的"（每次都花钱、每次结果不同），PUT 是"这个角色的基准图
    就是它"——幂等，同一个 asset_id 提交十次和一次结果完全一样。
    用同一个动词会让"点两下多扣一次钱"和"点两下没事"混在一起。
    """
    result = await render.assign_character_portrait(
        db,
        org_id=user.org_id,
        project_id=project_id,
        ref=ref,
        asset_id=payload.asset_id,
    )
    return BaseImageOut.model_validate(result)


@router.put("/scenes/{ref}", response_model=BaseImageOut)
async def set_scene_reference(
    project_id: uuid.UUID,
    payload: BaseImageIn,
    user: CurrentUser,
    db: DbSession,
    ref: str = Path(min_length=1, max_length=32),
) -> BaseImageOut:
    """把一张已有资产钉成场景的基准参考图。**不出图，不扣 Credits。**"""
    result = await render.assign_scene_reference(
        db,
        org_id=user.org_id,
        project_id=project_id,
        ref=ref,
        asset_id=payload.asset_id,
    )
    return BaseImageOut.model_validate(result)


@router.post("/shots/{shot_index}", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
async def generate_shot_image(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    payload: RenderIn | None = Body(None),
    shot_index: int = Path(ge=1),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> TaskOut:
    """给一个镜号出图。真实出图调用，会扣 Credits。"""
    task = await render.request_shot_image(
        db,
        org_id=user.org_id,
        project_id=project_id,
        created_by=user.id,
        shot_index=shot_index,
        source=(payload.source if payload else render.IMAGE_SOURCE_API),
        idempotency_key=idempotency_key,
        prompt_run_id=(payload.prompt_run_id if payload else None),
        instruction=(payload.instruction if payload else ""),
    )
    return TaskOut.model_validate(task)
