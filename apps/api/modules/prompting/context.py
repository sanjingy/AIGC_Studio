"""喂给提示词 Agent 的上下文（basis），以及它的摘要。**只读，不调模型。**

这个模块回答两个问题：

1. **这一次要把什么送给模型。** ADR-036 只说"最终提示词由 Agent 合成"，
   没说给它什么。给少了它就得自己编：编人种、编空间、编光。所以这里的原则
   是**把系统已经确定的东西全部送过去**——完整的风格描述词、门① 锁定的
   时代背景与人种、角色/场景的完整档案、镜头行上用户改过的每一个字段、
   空间锚点的固定层。缺关键前置数据时**不给默认值**，直接报错
   （ADR-037 第 2 条：判定结果不得默认套用本国）。

2. **这一份上下文有没有变过。** 摘要（`basis_digest`）是上下文的 sha256。
   它有两个用处：一份准备好的提示词能不能复用（摘要相同就能，不必再花一次
   推理），以及它是不是已经过期（用户改了景别、换了角色档案、动了锚点）。
   过期的提示词**不静默重算也不静默沿用**——旧词旧图都留着并标过期，
   由用户决定要不要重新准备。

`rule_version` 也进摘要：模板或校验规则变了，所有旧提示词都应当显示为
过期。它标的是"按哪一版规则生成的"，不是代码版本。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.errors import AppError
from apps.api.modules.consistency import compose
from apps.api.modules.consistency import service as consistency

# 类型也从对方的 **service** 层拿，不从 `consistency.models` 直接导
# （CLAUDE.md 硬规则：跨模块只调对方 service）。`service` 已经把这三个类
# 重新导出，所以这不是绕过规则的写法——`models` 是它的内部结构，
# 什么时候拆表、拆成几张，都不该让 `prompting` 跟着改。
from apps.api.modules.consistency.service import CharacterProfile, SceneProfile, StyleProfile
from apps.api.modules.project import service as project_service
from apps.api.modules.prompting import rules

#: 出图任务 `input_json.subject_kind` 的取值，与 `consistency.render` 用的是
#: 同一组字面量。这里不 import 它：那个模块反过来要 import 本模块，
#: 为三个字符串建一条循环依赖不划算。改动必须两处同时改。
SUBJECT_CHARACTER = "character"
SUBJECT_SCENE = "scene"
SUBJECT_SHOT = "shot"

#: 每一类提示词该用哪一套风格描述词（ADR-036 第 3 条，三者不得混用）。
#: 镜头图不在表里：它按"这一镜有没有人出场"动态选，见 `_shot_surface`。
_SURFACE: dict[str, str] = {
    rules.KIND_CHARACTER: "character",
    rules.KIND_SCENE: "scene",
    rules.KIND_SHOT_VIDEO: "video",
}

_SURFACE_LABEL: dict[str, str] = {
    "character": "人物版",
    "scene": "场景版",
    "video": "视频版",
}


@dataclass(frozen=True, slots=True)
class Basis:
    """一次提示词准备的全部输入。"""

    kind: str
    subject_key: str
    #: 送给模型的上下文，原样就是 `user_input` 的 JSON
    context: dict[str, Any]
    #: 上下文的 sha256，前 32 位十六进制
    digest: str
    #: 校验风格词时用的那一套（按 kind 与出场人物选出来的）
    style: dict[str, Any]
    #: 出图任务要写的 subject 字段
    subject_kind: str
    subject_ref: str | None
    shot_index: int | None
    #: 出图用的种子。视频提示词没有，为 None
    seed: int | None
    negative_prompt: str
    #: 这一镜用到的角色档案主键（字符串形式）。给 `record_conditioning` 用——
    #: 它记的是"这一镜实际拿哪几份档案去生成的"，而档案主键只有这里知道。
    #: 角色/场景两类提示词用不到，留空。
    character_profile_ids: list[str] = field(default_factory=list)
    #: 同上，这一镜用的场景档案主键。
    scene_profile_id: str | None = None
    #: 这次用的风格档案主键。`record_conditioning` 要它，而在这里顺手带出去
    #: 比让调用方再查一次库便宜，也不会出现"取词用的档案和记录里写的不是
    #: 同一份"这种对不上的情况。
    style_profile_id: str = ""


def digest_of(context: dict[str, Any]) -> str:
    """上下文的摘要。

    `sort_keys` 必须开：字典顺序在 Python 里稳定，但上下文是从 JSONB、
    ORM 对象和几份 Agent 产出拼起来的，键的插入顺序会随代码改动漂移，
    而漂移一次就是全项目的提示词集体显示过期。
    """
    payload = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _global_tone(state: dict[str, Any], style: StyleProfile) -> str:
    """这部片子的整体色调。

    优先用**场景阶段产出的那一个**（`SceneSheets.global_tone`，原 Skill B4）：
    它是模型读完全剧之后为这一部片子定的调子。画风目录上的
    `color_grading` 是那一套画风的通用值，同一种画风的所有项目共用一个，
    当不了"这部片子的色调"。

    取不到就回落到目录值——`global_tone` 是后加的字段，存量项目的场景产出
    里没有它。两个都空就是真的没有色调可写，返回空串，
    `_style_block` 因此不会往上下文里塞一个空键，`check_style` 也就不会去
    要求提示词里出现一段空文本。**不编一个色调出来。**
    """
    scenes = state.get("scenes")
    if isinstance(scenes, dict) and (tone := str(scenes.get("global_tone", "") or "").strip()):
        return tone
    return str(style.color_grading or "").strip()


def _style_block(
    style: StyleProfile, surface: str, *, with_tone: bool, tone: str = ""
) -> dict[str, Any]:
    """送给模型的风格描述词。

    `with_tone` 决定带不带全局色调：角色立绘**不带**——它是后续所有镜头的
    比对基准，带上戏剧化的色调会污染基准，让相似度失去意义（这条在
    `compose.reference_portrait_prompt` 的年代就是这样）。场景概念图与镜头
    带上，因为"所有场景看起来像同一部影片的不同镜头"正是全局色调的职责。

    色调是**传进来的**而不是在这里读 `style.color_grading`：它的第一来源是
    场景阶段的产出，只有存量数据才回落到画风目录（见 `_global_tone`）。
    """
    tokens = str(getattr(style, f"{surface}_tokens", "") or "")
    block: dict[str, Any] = {
        "surface": _SURFACE_LABEL[surface],
        "tokens": tokens,
        "render_mode": style.render_mode,
        "line_weight": style.line_weight,
    }
    if with_tone and tone:
        block["color_grading"] = tone
    return block


def _require_style(style: StyleProfile, surface: str) -> None:
    """这套风格描述词是不是空的。

    空的有两种来源：项目根本没过门① 就被人从别的入口触发了出图，或者
    画风目录里那一行的这一套描述词没填。两种都不能往下走——没有风格词就
    没有"必须原样保留"的东西可校验，出的图也不属于任何一部片子。
    """
    if not str(getattr(style, f"{surface}_tokens", "") or "").strip():
        raise AppError(
            "prompt.style.unlocked",
            message=f"项目的{_SURFACE_LABEL[surface]}画风描述词是空的",
            detail={"style_key": style.style_key, "surface": surface},
        )


async def _era_block(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, state: dict[str, Any]
) -> dict[str, str]:
    """时代背景、国别与人种。优先用门① 锁定的值。

    取不到就如实留空并把情节目录的判定带过去——**不在这里填一个默认人种**。
    需不需要具体人种是按 kind 判的（角色立绘需要，场景四视图不需要），
    判定在 `_character_basis` 里，不在这儿。
    """
    lock = await project_service.get_lock_variables(db, org_id=org_id, project_id=project_id)
    plot = state.get("plot_index") or {}
    if lock is not None:
        return {
            "era": lock.era,
            "region": lock.region,
            "ethnicity": lock.ethnicity,
            "evidence": lock.era_evidence,
            "confirmed": "已确认" if lock.confirmed_at is not None else "未确认",
        }
    return {
        "era": str(plot.get("era", "") or ""),
        "region": str(plot.get("region", "") or ""),
        "ethnicity": str(plot.get("ethnicity", "") or ""),
        "evidence": str(plot.get("era_evidence", "") or ""),
        "confirmed": "未确认",
    }


def _sheet_of(state: dict[str, Any], key: str, ref: str) -> dict[str, Any]:
    """从 Agent 产出里取某个 ref 的完整档案。取不到返回空字典。

    档案表（`character_profiles` / `scene_profiles`）只存了提示词需要的那
    几个字段，阵营、性格、人物关系、推断说明都不在里面。而 B3 的模板要按
    阵营决定气质与妆容，C2 的资产声明句要按人物关系写。所以这里从
    `current_state_json` 里把**整份 sheet** 取出来，档案表那一份只用来兜住
    "库里冻结的是哪一版"。
    """
    output = state.get(key)
    if not isinstance(output, dict):
        return {}
    items = output.get(key) if key in output else None
    if not isinstance(items, list):
        return {}
    for item in items:
        if isinstance(item, dict) and str(item.get("ref", "")) == ref:
            return dict(item)
    return {}


def _character_block(profile: CharacterProfile, sheet: dict[str, Any]) -> dict[str, Any]:
    """一个角色送给模型的样子：完整档案 + 冻结的结构化外貌。"""
    block = dict(sheet)
    block.update(
        {
            "ref": profile.ref,
            "name": profile.name,
            "version": profile.version,
            "appearance": dict(profile.appearance_json or {}),
        }
    )
    return block


def _scene_block(
    profile: SceneProfile, sheet: dict[str, Any], lighting_ref: str = ""
) -> tuple[dict[str, Any], dict[str, str]]:
    """一个场景送给模型的样子，外加这一次实际使用的光照状态。"""
    spatial = dict(profile.spatial_json or {})
    lighting = compose.resolve_lighting(spatial, lighting_ref)
    block = dict(sheet)
    block.update(
        {
            "ref": profile.ref,
            "name": profile.name,
            "version": profile.version,
            "time_slot": spatial.get("time_slot", ""),
            "setting": spatial.get("setting", ""),
            "key_elements": list(spatial.get("key_elements") or []),
            "camera_axis": dict(spatial.get("camera_axis") or {}),
            "fixed_references": list(spatial.get("fixed_references") or []),
            "lighting_states": list(spatial.get("lighting_states") or []),
            "default_lighting": spatial.get("default_lighting", ""),
        }
    )
    return block, lighting


def _require_camera_axis(scene: dict[str, Any]) -> None:
    """摄影主轴是场景一致性的全部依据，缺了就不该往下走。

    没有它，四视图的"正面"由模型每次自己挑，同一个场景两次生成会朝两个
    方向——那张图就当不了后续镜头的空间基准，而它存在的唯一理由就是当基准。
    """
    axis = dict(scene.get("camera_axis") or {})
    fields = (("position", "摄影机站位"), ("facing", "朝向"), ("far_end", "远景末端"))
    missing = [label for field, label in fields if not str(axis.get(field, "") or "").strip()]
    if missing:
        joined = "、".join(missing)
        raise AppError(
            "prompt.context.incomplete",
            message=f"场景「{scene.get('name') or scene.get('ref')}」的摄影主轴缺少：{joined}",
            detail={"scene_ref": scene.get("ref"), "missing": missing},
        )


async def _existing(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    profiles: list[Any],
    stage: str,
    label: str,
) -> tuple[StyleProfile, list[Any]]:
    """只读分支共用的收尾：拿库里现成的风格档案，缺什么就如实报缺。

    **一个字都不写。** 只读路径（`GET /prompts/...`）走这里，所以它不能
    `sync_*` 也不能 `commit`——见 `build(read_only=...)` 的说明。
    """
    style = await consistency.get_style(db, org_id=org_id, project_id=project_id)
    if style is None or not profiles:
        raise AppError(
            "consistency.profile.missing",
            message=f"项目还没有{label}档案",
            detail={"missing_stage": stage},
        )
    return style, profiles


async def _characters(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    state: dict[str, Any],
    read_only: bool,
) -> tuple[StyleProfile, list[CharacterProfile]]:
    if read_only:
        return await _existing(
            db,
            org_id=org_id,
            project_id=project_id,
            profiles=list(
                await consistency.list_characters(db, org_id=org_id, project_id=project_id)
            ),
            stage="characters",
            label="角色",
        )
    output = state.get("characters")
    if not isinstance(output, dict) or not output.get("characters"):
        raise AppError(
            "consistency.profile.missing",
            message="项目还没有角色档案产出",
            detail={"missing_stage": "characters"},
        )
    style, profiles = await consistency.sync_from_characters_output(
        db, org_id=org_id, project_id=project_id, output=output
    )
    await db.commit()
    if not profiles:
        raise AppError(
            "consistency.profile.missing",
            message="角色档案里没有可用的角色（缺 ref）",
            detail={"missing_stage": "characters"},
        )
    return style, profiles


async def _scenes(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    state: dict[str, Any],
    read_only: bool,
) -> tuple[StyleProfile, list[SceneProfile]]:
    if read_only:
        return await _existing(
            db,
            org_id=org_id,
            project_id=project_id,
            profiles=list(await consistency.list_scenes(db, org_id=org_id, project_id=project_id)),
            stage="scenes",
            label="场景",
        )
    output = state.get("scenes")
    if not isinstance(output, dict) or not output.get("scenes"):
        raise AppError(
            "consistency.profile.missing",
            message="项目还没有场景档案产出",
            detail={"missing_stage": "scenes"},
        )
    style, profiles = await consistency.sync_from_scenes_output(
        db, org_id=org_id, project_id=project_id, output=output
    )
    await db.commit()
    if not profiles:
        raise AppError(
            "consistency.profile.missing",
            message="场景档案里没有可用的场景（缺 ref）",
            detail={"missing_stage": "scenes"},
        )
    return style, profiles


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
            return dict(shot)
    raise AppError("common.not_found", message=f"shot {shot_index}")


def _shot_block(shot: dict[str, Any]) -> dict[str, Any]:
    """镜头行上**所有会影响画面的字段**，一个都不许漏。

    在这之前出图只传了 `content`——用户在分镜工作台上改的景别、角度、运镜
    全部丢在路上，界面显示改成功了，出来的图却和改之前一样。这是这次
    整改要修的第一件事，所以这里逐个列名而不是 `dict(shot)`：
    列名会让"分镜表加了一个字段但出图没吃到"在代码评审时看得见。
    """
    return {
        "index": int(shot.get("index", 0)),
        "node_index": shot.get("node_index"),
        "scene_ref": str(shot.get("scene_ref", "") or ""),
        "character_refs": [str(r) for r in (shot.get("character_refs") or [])],
        "shot_size": str(shot.get("shot_size", "") or ""),
        "angle": str(shot.get("angle", "") or ""),
        "camera_move": str(shot.get("camera_move", "") or ""),
        "content": str(shot.get("content", "") or ""),
        "lighting_ref": str(shot.get("lighting_ref", "") or ""),
        "speaker_ref": str(shot.get("speaker_ref", "") or ""),
        "dialogue": str(shot.get("dialogue", "") or ""),
        "sfx": str(shot.get("sfx", "") or ""),
    }


def _anchor_card(scene: dict[str, Any], lighting: dict[str, str]) -> dict[str, Any]:
    """空间锚点卡的固定层。动态层（人物站位）由模型按本镜动作推。"""
    return {
        "scene_name": scene.get("name", ""),
        "camera_axis": scene.get("camera_axis") or {},
        "fixed_references": scene.get("fixed_references") or [],
        "lighting_tone": lighting.get("description", ""),
    }


def _shot_surface(character_refs: list[str]) -> str:
    """镜头图用哪一套风格描述词。

    有人出场用人物版，空镜用场景版（ADR-036 第 3 条的判据：画面里有没有人
    是能从数据判出来的，不需要再让谁去选）。**首帧是静态图，所以不用视频版**
    ——视频版写的是帧率与运动质感，画静态图时是噪声。原 Skill 的 C4 九宫格
    用视频版，这一处是我们按 ADR-036 做的有意偏离，记在交付报告里。
    """
    return "character" if character_refs else "scene"


async def build(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    kind: str,
    subject_key: str,
    read_only: bool = False,
) -> Basis:
    """组装某个对象这一刻的上下文。**跨租户/跨项目在这里就 404 了。**

    `read_only=True` 时**一个字都不写库**：不 `sync_from_*_output`、不
    `commit`，档案直接读库里现成的那一版。

    这不是优化，是必须的。只读路径只有一条用途——回答"这份提示词还对不对
    得上现在的内容"。而 `sync_*` 会把 `current_state_json` 里的产出冻进
    `character_profiles` / `scene_profiles` 并提交：那意味着**用户点开看一眼
    就改了库**，档案 version 往前跳、`updated_at` 变了，而他什么都没做。
    更糟的是它让"看一眼"和"确认这一版"变成同一个动作，用户再也没法先看
    再决定。

    代价是只读算出的摘要用的是**库里那一版**档案：上游产出改过但还没被任何
    一次生成冻结时，摘要会和写入路径算出来的不同，于是这份提示词显示为过期。
    那恰好是对的——内容确实变过了。
    """
    if kind not in rules.KINDS:
        raise AppError(
            "common.validation_failed",
            message=f"未知的提示词类型 {kind!r}",
            detail={"allowed": list(rules.KINDS)},
        )
    project = await project_service.get_project(db, org_id=org_id, project_id=project_id)
    state = dict(project.current_state_json or {})
    era = await _era_block(db, org_id=org_id, project_id=project_id, state=state)

    if kind == rules.KIND_CHARACTER:
        return await _character_basis(
            db,
            org_id=org_id,
            project_id=project_id,
            state=state,
            era=era,
            ref=subject_key,
            read_only=read_only,
        )
    if kind == rules.KIND_SCENE:
        return await _scene_basis(
            db,
            org_id=org_id,
            project_id=project_id,
            state=state,
            era=era,
            ref=subject_key,
            read_only=read_only,
        )
    return await _shot_basis(
        db,
        org_id=org_id,
        project_id=project_id,
        state=state,
        era=era,
        kind=kind,
        subject_key=subject_key,
        read_only=read_only,
    )


def _shot_index_of(subject_key: str) -> int:
    """镜号的 subject_key 是十进制字符串。解析不了就是 404，不是 500。"""
    try:
        index = int(subject_key)
    except (TypeError, ValueError):
        raise AppError("common.not_found", message=f"shot {subject_key}") from None
    if index < 1:
        raise AppError("common.not_found", message=f"shot {subject_key}")
    return index


async def _character_basis(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    state: dict[str, Any],
    era: dict[str, str],
    ref: str,
    read_only: bool = False,
) -> Basis:
    style, profiles = await _characters(
        db, org_id=org_id, project_id=project_id, state=state, read_only=read_only
    )
    profile = next((p for p in profiles if p.ref == ref), None)
    if profile is None:
        # 角色不属于这个项目，和"项目不属于这个租户"一样只给 404
        raise AppError("common.not_found", message=f"character {ref}")
    _require_style(style, "character")

    character = _character_block(profile, _sheet_of(state, "characters", ref))

    # 关键身份缺失就报错，**不猜**。ADR-037 第 2 条写得很直白：判错会让人种、
    # 服装、发型与场景建筑一起错，且一路传导到分镜，返工成本是"全部重出"。
    if str(character.get("kind", "人类")) == "人类":
        # 与校验器共用同一份字段定义：抄成两份，迟早出现"组装时认为有身份、
        # 校验时认为没有"，而那种矛盾只会表现为一条谁都看不懂的报错。
        identity = rules.known_identity(character, era)
        if not identity:
            raise AppError(
                "prompt.context.incomplete",
                message=(
                    f"角色「{profile.name}」没有国籍/人种，项目也还没有确认时代背景，"
                    "无法生成立绘提示词"
                ),
                detail={"character_ref": ref, "missing": ["nationality", "ethnicity"]},
            )

    style_block = _style_block(style, "character", with_tone=False)
    context: dict[str, Any] = {
        "kind": rules.KIND_CHARACTER,
        "rule_version": rules.RULE_VERSION,
        "subject_key": ref,
        "style": style_block,
        "era": era,
        "character": character,
    }
    return Basis(
        kind=rules.KIND_CHARACTER,
        subject_key=ref,
        context=context,
        digest=digest_of(context),
        style=style_block,
        subject_kind=SUBJECT_CHARACTER,
        subject_ref=profile.ref,
        shot_index=None,
        # 立绘用项目基准种子本身，镜头才在它上面按镜号偏移。同一个角色重出
        # 立绘落在同一个种子上，便于对比改动。
        seed=style.seed_base or None,
        negative_prompt=style.negative_tokens,
        style_profile_id=str(style.id),
    )


async def _scene_basis(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    state: dict[str, Any],
    era: dict[str, str],
    ref: str,
    read_only: bool = False,
) -> Basis:
    style, profiles = await _scenes(
        db, org_id=org_id, project_id=project_id, state=state, read_only=read_only
    )
    profile = next((p for p in profiles if p.ref == ref), None)
    if profile is None:
        raise AppError("common.not_found", message=f"scene {ref}")
    _require_style(style, "scene")

    scene, lighting = _scene_block(profile, _sheet_of(state, "scenes", ref))
    _require_camera_axis(scene)

    style_block = _style_block(
        style, "scene", with_tone=True, tone=_global_tone(state, style)
    )
    context: dict[str, Any] = {
        "kind": rules.KIND_SCENE,
        "rule_version": rules.RULE_VERSION,
        "subject_key": ref,
        "style": style_block,
        "era": era,
        "scene": scene,
        # 概念图用**默认光照状态**：这张图是空间基准，同一场景的多种光照共用
        # 它，用哪一种当基准都不该由某一镜决定。
        "lighting": lighting,
    }
    return Basis(
        kind=rules.KIND_SCENE,
        subject_key=ref,
        context=context,
        digest=digest_of(context),
        style=style_block,
        subject_kind=SUBJECT_SCENE,
        subject_ref=profile.ref,
        shot_index=None,
        seed=style.seed_base or None,
        negative_prompt=style.negative_tokens,
        style_profile_id=str(style.id),
    )


async def _shot_basis(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    state: dict[str, Any],
    era: dict[str, str],
    kind: str,
    subject_key: str,
    read_only: bool = False,
) -> Basis:
    shot_index = _shot_index_of(subject_key)
    shot = _shot_block(_find_shot(state, shot_index))

    style, character_profiles = await _characters(
        db, org_id=org_id, project_id=project_id, state=state, read_only=read_only
    )
    by_ref = {p.ref: p for p in character_profiles}
    refs = list(dict.fromkeys(shot["character_refs"]))

    # 分镜表引用了档案里没有的角色。**这一条从"跳过并记 warning"改成了报错**：
    # 跳过意味着模型手里少一个人的外貌，而它仍然要把这一镜画出来——结果就是
    # 它自己编一个人。编出来的人下一镜又不一样，这正是这次整改要消灭的东西。
    if missing := [r for r in refs if r not in by_ref]:
        joined = "、".join(missing)
        raise AppError(
            "prompt.context.incomplete",
            message=f"第 {shot_index} 镜引用了没有档案的角色：{joined}",
            detail={"shot_index": shot_index, "missing_character_refs": missing},
        )

    _, scene_profiles = await _scenes(
        db, org_id=org_id, project_id=project_id, state=state, read_only=read_only
    )
    scene_profile = next((p for p in scene_profiles if p.ref == shot["scene_ref"]), None)
    if scene_profile is None:
        raise AppError(
            "prompt.context.incomplete",
            message=(
                f"第 {shot_index} 镜的场景「{shot['scene_ref'] or '（未填）'}」没有档案，"
                "无法推演身后空间"
            ),
            detail={"shot_index": shot_index, "scene_ref": shot["scene_ref"]},
        )

    scene, lighting = _scene_block(
        scene_profile, _sheet_of(state, "scenes", scene_profile.ref), shot["lighting_ref"]
    )
    _require_camera_axis(scene)

    surface = "video" if kind == rules.KIND_SHOT_VIDEO else _shot_surface(refs)
    _require_style(style, surface)

    style_block = _style_block(
        style, surface, with_tone=True, tone=_global_tone(state, style)
    )
    context: dict[str, Any] = {
        "kind": kind,
        "rule_version": rules.RULE_VERSION,
        "subject_key": str(shot_index),
        "style": style_block,
        "era": era,
        "shot": shot,
        "characters": [
            _character_block(by_ref[r], _sheet_of(state, "characters", r)) for r in refs
        ],
        "scene": scene,
        "lighting": lighting,
        "anchor_card": _anchor_card(scene, lighting),
    }
    return Basis(
        kind=kind,
        subject_key=str(shot_index),
        context=context,
        digest=digest_of(context),
        style=style_block,
        subject_kind=SUBJECT_SHOT,
        subject_ref=None,
        shot_index=shot_index,
        # 项目级基准 seed + 镜号偏移。视频提示词不出图，种子对它没有意义。
        seed=(
            None
            if kind == rules.KIND_SHOT_VIDEO
            else ((style.seed_base + shot_index) if style.seed_base else None)
        ),
        negative_prompt=style.negative_tokens,
        character_profile_ids=[str(by_ref[r].id) for r in refs],
        scene_profile_id=str(scene_profile.id),
        style_profile_id=str(style.id),
    )
