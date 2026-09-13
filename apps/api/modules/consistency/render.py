"""从产品界面触发一次真实出图。

在这之前，出图能力是"有后端无 UI"：`worker/jobs/generation.py` 能出图，
`compose.py` 能合成提示词，但**没有任何一条路径把两者接起来**——
唯一跑通过的是 `scripts/validation_slice.py`，它绕开了 UI、任务队列和
计费。这个模块就是那条缺失的路径。

三条规则决定了这里的所有设计：

1. **提示词由提示词 Agent 合成，前端传不了。** 画面描述不作数，风格词
   永远由系统逐字注入并校验是否被原样保留（ADR-036 / 17_ConsistencyEngine
   §4）。所以这几个端点的入参只有"要画哪个角色 / 哪一镜"、可选的创作要求，
   以及可选的"用我刚才看过的那一版提示词"（`prompt_run_id`），没有 prompt。

   **这里以前调的是 `compose` 的确定性拼接**（`reference_portrait_prompt` /
   `reference_scene_prompt` / `compose_shot`），ADR-036 把那条路整体换掉了。
   `compose.py` 本身保留（`scripts/validation_slice.py` 和既有单测还在用），
   但它**已经不在生产出图路径上**——见该模块的 docstring。
2. **走同一条任务路径。** 建任务、预扣、入队、结算全部交给
   `task.service.create_task`，这里一行状态机逻辑都不写——
   执行状态的唯一真相是 `tasks.status`（ADR-008）。
3. **前置条件缺失要在花钱之前拦下来。** 没有角色档案就没有风格档案，
   合成不出提示词。这时候返回 `consistency.profile.missing`，
   而不是让用户拿到一个跑到 Worker 才炸的 500。

后来多了第四条规则，因为多了一条不生成的路径：

4. **"用一张已有的图"不是"生成一张图"。** `assign_*` 把用户指定的
   `asset_id` 直接钉成基准图，不建任务、不预扣、不结算——上面三条里
   除了"跨租户 404"之外的每一条都不适用于它。两条路径在数据里靠
   `Render.source` 分开，不靠前端猜。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.errors import AppError
from apps.api.core.logging import get_logger
from apps.api.modules.asset import service as asset_service
from apps.api.modules.consistency import compose
from apps.api.modules.consistency import service as consistency
from apps.api.modules.consistency.models import CharacterProfile, SceneProfile, StyleProfile
from apps.api.modules.project import service as project_service
from apps.api.modules.prompting import rules as prompt_rules
from apps.api.modules.task import service as task_service
from apps.api.modules.task.models import Task

log = get_logger(__name__)

IMAGE_TASK = "image.generate"

# 出图尺寸。写死在这里不违反"价格不进代码"——它是画面规格不是价格，
# 计费按张数算（pricing._shape 只看 n）。
DEFAULT_SIZE = "1024*1024"

# 每一类出图要的画幅。
#
# **提示词里写的比例和请求参数必须是同一个。** 原 Skill 的 B3 模板末尾写
# 「9:16」、B5 写「16:9」，而在这之前三条路径一律按 `DEFAULT_SIZE` 发方图：
# 模型一边被告知画竖图、一边被要求填满一个正方形画布，构图必然被裁或被拉，
# 而这张图是后续所有镜头的基准。
#
# 取值是万相 t2i 文档里的标准档（`catalog.SPECS` 现在挂的是
# `wan2.2-t2i-flash` / `wan2.2-t2i-plus`）。**没有按模板里那句「2K高清」
# 去凑一个 2K 尺寸**：那需要确认这两个模型到底收不收，猜一个填进去只会在
# 上游换成参数错误。模板文案保持原文不动（源模板逐字照抄是硬规则），
# 与实际画幅的这点落差记在交付报告里。
#
# 镜头首帧维持方图：分镜的既有比例就是它，这一轮不动。
SIZE_OF_KIND: dict[str, str] = {
    prompt_rules.KIND_CHARACTER: "720*1280",  # 9:16 竖图，B3 模板末尾那句
    prompt_rules.KIND_SCENE: "1280*720",  # 16:9 横图，B5 模板末尾那句
    prompt_rules.KIND_SHOT_IMAGE: DEFAULT_SIZE,
}

SUBJECT_CHARACTER = "character"
SUBJECT_SCENE = "scene"
SUBJECT_SHOT = "shot"

# 这张图是怎么来的。
#
# `generated` 是上面那三条路：建任务、预扣、跑 Provider、结算。
# `assigned` 是用户自己指定的一张既有资产（从资产库挑的，或者刚从本地
# 传上来的）——**它不经过 Gateway，所以不建任务、不预扣、不结算**。
# 两者在界面上长得一样（都是"这个角色现在的基准图"），但计费语义完全
# 相反，所以必须在数据里分得开，不能让前端靠"有没有 task_id"去猜。
SOURCE_GENERATED = "generated"
SOURCE_ASSIGNED = "assigned"

# 这张图是**谁**画的。
#
# `api` 是平台的 Provider 网关（万相等），花的是平台 Credits 对应的上游成本；
# `local` 是用户自己电脑上的 Codex，花的是他自己的订阅额度。两条路的产物
# 落在同一张 `tasks` 行、同一个资产库，但**代价的承担者不同**，所以必须
# 在任务建起来的那一刻就钉死在 payload 里，不能等执行时再看"哪个可用"。
#
# 钉死的另一个理由更硬：选了本机就**不许**回落到付费 API。运行时再决定
# 等于给了它一个悄悄替用户花钱的机会。
IMAGE_SOURCE_API = "api"
IMAGE_SOURCE_LOCAL = "local"
IMAGE_SOURCES = (IMAGE_SOURCE_API, IMAGE_SOURCE_LOCAL)

# 一次列多少条出图记录。26 个镜号 + 若干角色立绘 + 若干场景参考图，
# 再加上重试，100 条足够覆盖一个项目的全部出图，且不用分页。
MAX_RENDERS = 100


@dataclass(frozen=True, slots=True)
class Render:
    """一次出图的对外视图：谁、跑到哪一步、图在哪。

    它不是新的一份状态，而是 `tasks` 那一行的投影——
    状态永远从 `tasks.status` 读（ADR-008），这里只负责把
    "这个任务画的是哪个角色/哪一镜"和"产出的资产 id"翻译出来。
    """

    #: `assigned` 那条路没有任务，所以这里可空——它是"用户钉了一张图"，
    #: 不是"系统跑了一次生成"，硬造一个任务 id 只会让重试按钮点得下去。
    task_id: uuid.UUID | None
    subject_kind: str
    subject_ref: str | None
    shot_index: int | None
    status: str
    progress: int
    error_code: str | None
    asset_id: uuid.UUID | None
    created_at: datetime
    #: SOURCE_GENERATED / SOURCE_ASSIGNED
    source: str = SOURCE_GENERATED
    #: 这一张是谁画的：`api`（平台 Provider）还是 `local`（用户自己的 Codex）。
    #: 用户自己钉上去的那种没人画，所以是 None。界面据此标注来源——
    #: 两条路的代价承担者不同，不标出来用户没法判断"这张图花了谁的钱"。
    image_source: str | None = None


@dataclass(frozen=True, slots=True)
class BaseImage:
    """把一张既有资产钉成基准图之后的回执。

    刻意不返回 `TaskOut`：那个形状里有 `estimated_cost`、`attempt`、
    `status`，全都不适用——这条路径一分钱都没花，也没有东西在跑。
    返回一个任务形状会让前端（和读日志的人）以为它是一次生成。
    """

    subject_kind: str
    subject_ref: str
    asset_id: uuid.UUID
    updated_at: datetime


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


async def _scene_profiles(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, state: dict[str, Any]
) -> tuple[StyleProfile, list[SceneProfile]]:
    """拿到这个项目的风格档案和场景资产包，必要时按当前 state 补齐。

    和 `_profiles` 同形同理：编排器接场景一致性是这次才加的，在那之前
    跑过场景阶段的项目库里一条场景档案都没有；聊天修订改过场景之后，
    `state` 里的空间描述也比库里新。两个写入动作都幂等，可以无条件调。
    """
    scenes_output = state.get("scenes")
    if not isinstance(scenes_output, dict) or not scenes_output.get("scenes"):
        raise AppError(
            "consistency.profile.missing",
            message="项目还没有场景档案产出",
            detail={"missing_stage": "scenes"},
        )

    style, profiles = await consistency.sync_from_scenes_output(
        db, org_id=org_id, project_id=project_id, output=scenes_output
    )
    await db.commit()

    if not profiles:
        raise AppError(
            "consistency.profile.missing",
            message="场景档案里没有可用的场景（缺 ref）",
            detail={"missing_stage": "scenes"},
        )
    return style, profiles


def _resolve_source(source: str) -> str:
    """校验来源取值。**判活不在这里。**

    以前这个函数还会顺手对本机来源做一次 preflight。判死本身是对的
    （建了任务就要预扣，预扣完再失败还得退，用户还会在任务中心看到一条
    本来就不该存在的失败记录），但它长在三个出图端点上就只是**入口守卫**，
    `retry_task` 和 `POST /tasks` 两条合法路径绕得过去。

    所以那道闸整体搬进了 `task_service.preflight_local_image`——所有建任务
    的路径都要经过 `create_task`，所有重试都要经过 `retry_task`，放在那里
    它才是不变式。这里只剩"这个取值认不认得"，是纯参数校验，不碰 IO。
    """
    if source not in IMAGE_SOURCES:
        raise AppError(
            "common.validation_failed",
            message=f"unknown image source {source}",
            detail={"allowed": list(IMAGE_SOURCES)},
        )
    return source


async def _replayed(
    db: AsyncSession, *, org_id: uuid.UUID, idempotency_key: str | None
) -> Task | None:
    """幂等重放命中的那条任务。**必须在做任何昂贵的事情之前问。**

    `create_task` 里本来就有这一步，但它在入参组装完之后才跑。自从提示词
    改由 Agent 合成（ADR-036），"组装入参"就等于**一次真实的模型调用**——
    等 `create_task` 去短路，用户重发一次请求就白花一次推理，而幂等键的
    全部意义就是"重发不该有副作用"。
    """
    if not idempotency_key:
        return None
    return await task_service.find_by_idempotency_key(db, org_id=org_id, key=idempotency_key)


async def _resolve_prompt(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    kind: str,
    subject_key: str,
    source: str,
    prompt_run_id: uuid.UUID | None,
    instruction: str,
) -> tuple[str, Any, Any]:
    """校验来源 → 离线预检 → 取成品提示词。**这三步的顺序不能换。**

    预检必须排在取词之前：选了「本机出图」而连接器不在线时，这一次出图
    从一开始就不该发生——先推理再发现连不上，等于白花一次钱，而且用户
    还会在任务中心看到一条本来就不该存在的失败记录。

    取词可能真的调一次模型（没点名 `prompt_run_id`、也没有可复用的历史
    版本时）。校验不过就在这里抛，**不建任务、不预扣、不出图**，也不退回
    `compose` 的旧拼接兜底——退回一次，用户拿到的是一张与全片画风无关的图，
    而且没有任何地方会告诉他。
    """
    source = _resolve_source(source)
    await task_service.preflight_local_image(
        org_id=org_id,
        project_id=project_id,
        task_type=IMAGE_TASK,
        input_json={task_service.IMAGE_SOURCE_KEY: source, "n": 1},
    )
    # 延迟导入：`prompting.context` 反过来要 import `consistency.service`，
    # 放在模块顶层会撞循环导入。
    from apps.api.modules.prompting import service as prompting

    prompt, basis = await prompting.resolve_for_render(
        db,
        org_id=org_id,
        project_id=project_id,
        kind=kind,
        subject_key=subject_key,
        prompt_run_id=prompt_run_id,
        instruction=instruction,
    )
    return source, prompt, basis


def _payload(
    prompt: Any, basis: Any, *, project_id: uuid.UUID, created_by: uuid.UUID, source: str
) -> dict[str, Any]:
    """出图任务的 `input_json`。

    `prompt` 是**模型产出的完整最终词**，不是由当前档案重新拼出来的——
    重试沿用这份 `input_json`，所以重试不会重新推理，也就不会因为档案在
    这期间变过而漂移到另一张图上。

    另外三个键是这张图的血缘：哪一次推理写的词（`prompt_run_id`）、
    按哪一版模板与校验规则（`rule_version`）、依据的内容摘要
    （`basis_digest`）。缺了它们，"这张图为什么长这样"只能靠猜。
    """
    payload: dict[str, Any] = {
        "prompt": prompt.prompt,
        "negative_prompt": prompt.negative_prompt,
        # 画幅跟着 kind 走，与提示词里写的比例一致（见 `SIZE_OF_KIND`）。
        "size": SIZE_OF_KIND.get(basis.kind, DEFAULT_SIZE),
        "n": 1,
        "seed": basis.seed,
        "project_id": str(project_id),
        "owner_user_id": str(created_by),
        "image_source": source,
        "subject_kind": basis.subject_kind,
        "prompt_run_id": str(prompt.run_id),
        "rule_version": prompt.rule_version,
        "basis_digest": prompt.basis_digest,
    }
    if basis.subject_ref is not None:
        payload["subject_ref"] = basis.subject_ref
    if basis.shot_index is not None:
        payload["shot_index"] = basis.shot_index
    return payload


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
    source: str = IMAGE_SOURCE_API,
    idempotency_key: str | None = None,
    prompt_run_id: uuid.UUID | None = None,
    instruction: str = "",
) -> Task:
    """给一个角色出基准立绘（B3）。

    立绘是后续所有镜头的一致性基准。提示词由 `visual.character_prompt.v1`
    合成——人类/非人类两套模板、四铁律、无任何表情、中性光照、纯色背景，
    任何戏剧化的光影都会污染基准。全局色调**不注入**：基准图带上它，
    相似度比对就失去了意义。

    `prompt_run_id` 是"用我刚才在界面上看过的那一版词"。不给就自动准备
    （同样的输入有合格历史版本时直接复用，不重复花一次推理），
    所以不带请求体的老按钮照常能用——但走的是新 Agent，不是旧拼接。
    """
    if (replay := await _replayed(db, org_id=org_id, idempotency_key=idempotency_key)) is not None:
        return replay

    # 项目先取：跨租户在这里就 404，早于任何推理。
    project = await project_service.get_project(db, org_id=org_id, project_id=project_id)
    source, prompt, basis = await _resolve_prompt(
        db,
        org_id=org_id,
        project_id=project_id,
        kind=prompt_rules.KIND_CHARACTER,
        subject_key=ref,
        source=source,
        prompt_run_id=prompt_run_id,
        instruction=instruction,
    )
    task = await _create(
        db,
        org_id=org_id,
        project=project,
        created_by=created_by,
        payload=_payload(
            prompt, basis, project_id=project_id, created_by=created_by, source=source
        ),
        idempotency_key=idempotency_key,
    )
    log.info(
        "consistency.portrait_requested",
        project_id=str(project_id),
        ref=ref,
        task_id=str(task.id),
        prompt_run_id=str(prompt.run_id),
    )
    return task


async def request_scene_reference(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    created_by: uuid.UUID,
    ref: str,
    source: str = IMAGE_SOURCE_API,
    idempotency_key: str | None = None,
    prompt_run_id: uuid.UUID | None = None,
    instruction: str = "",
) -> Task:
    """给一个场景出基准参考图（B5：2×2 四视图）。

    这张图之于场景，等同于基准立绘之于角色：同一场景后续所有镜头的空间
    基准。提示词由 `visual.scene_prompt.v1` 合成——2×2 四格、四个固定机位、
    元素锁定清单逐项照抄、人物排除的三重否定、无文字标注。摄影主轴缺失
    时**不出图**（`prompt.context.incomplete`）：没有主轴，四格的"正面"
    由模型每次自己挑，这张图就当不了基准，而当基准是它存在的唯一理由。
    """
    if (replay := await _replayed(db, org_id=org_id, idempotency_key=idempotency_key)) is not None:
        return replay

    project = await project_service.get_project(db, org_id=org_id, project_id=project_id)
    source, prompt, basis = await _resolve_prompt(
        db,
        org_id=org_id,
        project_id=project_id,
        kind=prompt_rules.KIND_SCENE,
        subject_key=ref,
        source=source,
        prompt_run_id=prompt_run_id,
        instruction=instruction,
    )
    task = await _create(
        db,
        org_id=org_id,
        project=project,
        created_by=created_by,
        payload=_payload(
            prompt, basis, project_id=project_id, created_by=created_by, source=source
        ),
        idempotency_key=idempotency_key,
    )
    log.info(
        "consistency.scene_reference_requested",
        project_id=str(project_id),
        ref=ref,
        task_id=str(task.id),
        prompt_run_id=str(prompt.run_id),
    )
    return task


async def _image_asset(db: AsyncSession, *, org_id: uuid.UUID, asset_id: uuid.UUID) -> Any:
    """校验一份资产能不能当基准图，能就把它取出来。

    两道闸，顺序不能换：

    1. **归属**。走 `asset.service.get_asset`，跨租户/不存在一律 404
       ——和项目、角色那两处一字不差，403 会确认资源存在。
    2. **可用性**。必须是图片，且上传已经 `complete`。`pending` 的记录
       在桶里可能一个字节都没有（用户选完文件就关了页面），把它钉成
       基准图等于让后续每一镜都拿着一张打不开的参考图去生成。

    第 2 道给 400 不给 404：那份资产确实存在、确实是他自己的，
    只是不合用。报 404 他会以为自己选错了图，回去再选一遍还是同一个。
    """
    asset = await asset_service.get_asset(db, org_id=org_id, asset_id=asset_id)

    if not str(asset.mime_type or "").lower().startswith("image/"):
        raise AppError(
            "consistency.base_image.invalid",
            message=f"asset {asset_id} is not an image ({asset.mime_type})",
            detail={"reason": "not_image", "mime_type": asset.mime_type},
        )
    if asset.status != "ready":
        raise AppError(
            "consistency.base_image.invalid",
            message=f"asset {asset_id} is not ready ({asset.status})",
            detail={"reason": "not_ready", "status": asset.status},
        )
    return asset


async def assign_character_portrait(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    ref: str,
    asset_id: uuid.UUID,
) -> BaseImage:
    """把一张**已有**的资产钉成某个角色的基准立绘。

    `request_character_portrait` 的姊妹函数，区别只有一个但它是根本的：
    **这里不问 Gateway 要图**。用户已经有一张满意的图（自己画的、从别的
    项目搬过来的、刚从本地传上来的），他要的是"就用这张"，不是"再生成
    一张像这样的"。所以这条路径不建任务、不预扣、不结算——
    `billing` 模块在这个函数的调用链上一行都不会被执行。

    `version` **不递增**。递增版本的含义是"角色设定本身改了"，后续镜头
    要按新版重出；换一张基准图不是设定变了，外貌字段一个字没动。
    把它当成新版本会让所有已生成的镜头凭空变成"过期版本"。

    换掉之前那张也**不删**旧资产：用户很可能想换回去，而删除是另一件事。
    """
    _project, state = await _project_state(db, org_id=org_id, project_id=project_id)
    _style, profiles = await _profiles(db, org_id=org_id, project_id=project_id, state=state)

    profile = next((p for p in profiles if p.ref == ref), None)
    if profile is None:
        raise AppError("common.not_found", message=f"character {ref}")

    await _image_asset(db, org_id=org_id, asset_id=asset_id)

    profile.base_portrait_asset_id = asset_id
    await db.commit()
    await db.refresh(profile)

    log.info(
        "consistency.portrait_assigned",
        project_id=str(project_id),
        ref=profile.ref,
        asset_id=str(asset_id),
    )
    return BaseImage(
        subject_kind=SUBJECT_CHARACTER,
        subject_ref=profile.ref,
        asset_id=asset_id,
        updated_at=profile.updated_at,
    )


async def assign_scene_reference(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    ref: str,
    asset_id: uuid.UUID,
) -> BaseImage:
    """把一张**已有**的资产钉成某个场景的基准参考图。

    与 `assign_character_portrait` 逐条同理，包括不建任务、不预扣、
    不递增 `version`、不删旧图。角色和场景两边必须完全对称——
    一边能挑库存图另一边只能生成，用户会当成两个不同的产品。
    """
    _project, state = await _project_state(db, org_id=org_id, project_id=project_id)
    _style, profiles = await _scene_profiles(db, org_id=org_id, project_id=project_id, state=state)

    profile = next((p for p in profiles if p.ref == ref), None)
    if profile is None:
        raise AppError("common.not_found", message=f"scene {ref}")

    await _image_asset(db, org_id=org_id, asset_id=asset_id)

    profile.base_reference_asset_id = asset_id
    await db.commit()
    await db.refresh(profile)

    log.info(
        "consistency.scene_reference_assigned",
        project_id=str(project_id),
        ref=profile.ref,
        asset_id=str(asset_id),
    )
    return BaseImage(
        subject_kind=SUBJECT_SCENE,
        subject_ref=profile.ref,
        asset_id=asset_id,
        updated_at=profile.updated_at,
    )


async def request_shot_image(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    created_by: uuid.UUID,
    shot_index: int,
    source: str = IMAGE_SOURCE_API,
    idempotency_key: str | None = None,
    prompt_run_id: uuid.UUID | None = None,
    instruction: str = "",
) -> Task:
    """给一个镜号出首帧（C4 五要素的单镜适配）。

    提示词由 `visual.shot_frame_prompt.v1` 合成，喂给它的是分镜表上
    **所有会影响画面的字段**——景别、角度、运镜、画面内容、出场人物、
    场景、光照状态。在这之前只传了 `content`，用户在分镜工作台上改的景别
    和角度全部丢在路上：界面显示改成功了，出来的图和改之前一样。

    **缺场景档案、或分镜引用了没有档案的角色，一律 409 不出图**
    （`prompt.context.incomplete`）。这两条以前都是"跳过并记 warning"：
    跳过意味着模型手里少一份空间或少一个人的外貌，而它仍然要把这一镜画
    出来——结果是它自己编，编出来的下一镜又不一样。参数缺失时不猜关键
    身份是 ADR-037 第 2 条，这里是它在出图端的落点。
    """
    if (replay := await _replayed(db, org_id=org_id, idempotency_key=idempotency_key)) is not None:
        return replay

    project = await project_service.get_project(db, org_id=org_id, project_id=project_id)
    source, prompt, basis = await _resolve_prompt(
        db,
        org_id=org_id,
        project_id=project_id,
        kind=prompt_rules.KIND_SHOT_IMAGE,
        subject_key=str(shot_index),
        source=source,
        prompt_run_id=prompt_run_id,
        instruction=instruction,
    )
    task = await _create(
        db,
        org_id=org_id,
        project=project,
        created_by=created_by,
        payload=_payload(
            prompt, basis, project_id=project_id, created_by=created_by, source=source
        ),
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
    #
    # `resolved_prompt` 存的是模型产出的全文，不是 compose 的拼接结果。
    await consistency.record_conditioning(
        db,
        org_id=org_id,
        project_id=project_id,
        shot_index=shot_index,
        composed=compose.Composed(
            prompt=prompt.prompt,
            negative_prompt=prompt.negative_prompt,
            seed=basis.seed,
            character_ids=list(basis.character_profile_ids),
            scene_id=basis.scene_profile_id,
        ),
        style_id=uuid.UUID(basis.style_profile_id),
        attempt=task.attempt + 1,
    )
    await db.commit()

    log.info(
        "consistency.shot_requested",
        project_id=str(project_id),
        shot_index=shot_index,
        characters=len(basis.character_profile_ids),
        task_id=str(task.id),
        prompt_run_id=str(prompt.run_id),
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


async def _assigned_base_images(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> list[Render]:
    """用户自己钉上去的基准图，翻译成和生成记录同一个形状。

    它们不在 `tasks` 里——那张表是执行状态的唯一真相（ADR-008），而钉一张
    既有图根本没有"执行"。但界面上它们和生成出来的图占同一个位置，所以
    必须从同一个接口出去，否则前端要维护两份"这个角色现在的图是哪张"。

    时间取档案行的 `updated_at`，不是 `now()`：这样这条记录和生成任务
    能放在同一根时间轴上排序——先钉后生成，生成的那张在前；先生成后钉，
    钉的那张在前。**当前那张图永远是最新的那条**，两个方向都成立。
    """
    out: list[Render] = []
    for character in await consistency.list_characters(db, org_id=org_id, project_id=project_id):
        if character.base_portrait_asset_id is None:
            continue
        out.append(
            Render(
                task_id=None,
                subject_kind=SUBJECT_CHARACTER,
                subject_ref=character.ref,
                shot_index=None,
                status="succeeded",
                progress=100,
                error_code=None,
                asset_id=character.base_portrait_asset_id,
                created_at=character.updated_at,
                source=SOURCE_ASSIGNED,
            )
        )
    for scene in await consistency.list_scenes(db, org_id=org_id, project_id=project_id):
        if scene.base_reference_asset_id is None:
            continue
        out.append(
            Render(
                task_id=None,
                subject_kind=SUBJECT_SCENE,
                subject_ref=scene.ref,
                shot_index=None,
                status="succeeded",
                progress=100,
                error_code=None,
                asset_id=scene.base_reference_asset_id,
                created_at=scene.updated_at,
                source=SOURCE_ASSIGNED,
            )
        )
    return out


async def list_renders(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> list[Render]:
    """列出这个项目的全部出图，最新的在前。

    两个来源合起来：`tasks` 里的生成记录，加上用户自己钉的基准图
    （见 `_assigned_base_images`）。合并之后统一按时间倒序，前端仍然
    "取第一条就是当前这一版"，不需要知道有两个来源。

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
        if kind not in (SUBJECT_CHARACTER, SUBJECT_SCENE, SUBJECT_SHOT):
            # 从 /tasks 直接建的出图任务没有 subject 标记，
            # 它不属于任何角色或镜号，列出来只会让前端不知道往哪贴
            continue
        raw_index = payload.get("shot_index")
        out.append(
            Render(
                task_id=row.id,
                source=SOURCE_GENERATED,
                subject_kind=kind,
                subject_ref=str(payload["subject_ref"]) if payload.get("subject_ref") else None,
                shot_index=int(raw_index) if isinstance(raw_index, int) else None,
                status=row.status,
                progress=row.progress,
                error_code=row.error_code,
                asset_id=_asset_id_of(row.output_json),
                created_at=row.created_at,
                # 老任务的 payload 里没有这个键（本机来源是后加的），
                # 缺省按 api 读——它们确实都是平台画的。
                image_source=str(payload.get("image_source") or IMAGE_SOURCE_API),
            )
        )

    out.extend(await _assigned_base_images(db, org_id=org_id, project_id=project_id))
    out.sort(key=lambda r: r.created_at, reverse=True)
    return out
