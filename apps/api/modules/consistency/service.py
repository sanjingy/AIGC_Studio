"""一致性引擎的唯一对外入口。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents import schemas as agent_schemas
from apps.api.core.errors import AppError
from apps.api.core.logging import get_logger
from apps.api.modules.consistency import compose, metrics

# 这三个 ORM 类是本模块**对外契约的一部分**：`prompting` 要按它们标注类型，
# 而跨模块只能调对方 service（CLAUDE.md 硬规则），不能去摸 `consistency.models`。
# `X as X` 是 PEP 484 的显式再导出写法——写成普通 import，mypy 的
# no_implicit_reexport 会判定它没被导出，跨模块标注就用不了。
from apps.api.modules.consistency.models import CharacterProfile as CharacterProfile
from apps.api.modules.consistency.models import SceneProfile as SceneProfile
from apps.api.modules.consistency.models import (
    ShotConditioning,
    ShotQualityScore,
    StyleCatalogEntry,
)
from apps.api.modules.consistency.models import StyleProfile as StyleProfile

log = get_logger(__name__)

# 缺省画风。**只是"目录里没写 sort_order 时先用哪一条"**，不是一份代码里的
# 画风定义——画风目录在 `style_catalog` 表里（见 `models.StyleCatalogEntry`），
# 加一种画风、改一句描述词都不该发版。
DEFAULT_STYLE_KEY = "anime_suspense"


async def list_style_catalog(
    db: AsyncSession, *, include_inactive: bool = False
) -> list[StyleCatalogEntry]:
    """可选画风目录，按展示顺序。

    全局表，不带 org_id：画风是平台内容，不是租户数据。跨租户在这里
    没有可泄露的东西——所有人看到的是同一份目录。
    """
    stmt = select(StyleCatalogEntry).where(StyleCatalogEntry.deleted_at.is_(None))
    if not include_inactive:
        stmt = stmt.where(StyleCatalogEntry.is_active.is_(True))
    stmt = stmt.order_by(StyleCatalogEntry.sort_order, StyleCatalogEntry.key)
    return list((await db.execute(stmt)).scalars())


async def get_style_entry(db: AsyncSession, *, key: str) -> StyleCatalogEntry | None:
    """按 key 取目录项。下架的也能取到——已经锁定它的项目还要能读回描述词。"""
    return (
        await db.execute(
            select(StyleCatalogEntry).where(
                StyleCatalogEntry.key == key, StyleCatalogEntry.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()


async def _resolve_entry(db: AsyncSession, key: str | None) -> StyleCatalogEntry:
    """把一个画风 key 解析成目录项。

    解析不到就报错，**不静默退回缺省画风**：用户在门① 选了一种画风，
    结果全片按另一种画风出图，而界面上还显示着他选的那个——这种错
    只有在成片出来之后才看得出来，那时所有图都要重出。

    key 为空是另一回事：那是"还没选过"（存量项目、或门① 之前的调用），
    此时用缺省画风是唯一能做的事，不是掩盖错误。
    """
    if key:
        entry = await get_style_entry(db, key=key)
        if entry is None:
            raise AppError("provider.params.invalid", message=f"未知画风 {key}")
        return entry

    entries = await list_style_catalog(db)
    if not entries:
        raise AppError(
            "common.internal",
            message="画风目录为空，style_catalog 没有种子数据",
        )
    default = next((e for e in entries if e.key == DEFAULT_STYLE_KEY), None)
    return default or entries[0]


async def ensure_style(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    preset: str | None = None,
    seed_base: int = 100_000,
) -> StyleProfile:
    """取项目的风格档案，没有就按目录里的画风创建。

    已存在就直接返回**不覆盖**——风格一旦有镜头产出即冻结，
    静默改掉会让前后镜头画风不一致且无从察觉。

    `preset=None` 时读项目在门① 锁定的画风（`project_lock_variables.style_key`）。
    这样四个调用点（角色阶段、场景阶段、三个出图端点）不用各自把 key 传一遍——
    传参数的写法只要漏一处，那一处就会按缺省画风建档，而它建完就冻结了。
    """
    existing = (
        await db.execute(
            select(StyleProfile)
            .where(StyleProfile.project_id == project_id, StyleProfile.deleted_at.is_(None))
            .order_by(StyleProfile.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    key = preset
    if key is None:
        # 延迟导入：project.service 不依赖 consistency，这一侧单向依赖不成环。
        from apps.api.modules.project import service as project_service

        lock = await project_service.get_lock_variables(db, org_id=org_id, project_id=project_id)
        key = lock.style_key if lock is not None else ""

    entry = await _resolve_entry(db, key)
    row = StyleProfile(
        org_id=org_id,
        project_id=project_id,
        seed_base=seed_base,
        style_key=entry.key,
        base_model=entry.base_model,
        character_tokens=entry.character_tokens,
        scene_tokens=entry.scene_tokens,
        video_tokens=entry.video_tokens,
        negative_tokens=entry.negative_tokens,
        color_grading=entry.color_grading,
        line_weight=entry.line_weight,
        render_mode=entry.render_mode,
    )
    db.add(row)
    await db.flush()
    return row


async def get_style(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> StyleProfile | None:
    """取项目当前的风格档案。没有就返回 None，不代建——
    "有没有跑过角色那一步"是调用方要判断的事，这里只回答事实。"""
    return (
        await db.execute(
            select(StyleProfile)
            .where(
                StyleProfile.org_id == org_id,
                StyleProfile.project_id == project_id,
                StyleProfile.deleted_at.is_(None),
            )
            .order_by(StyleProfile.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def list_characters(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> list[CharacterProfile]:
    """项目下的角色资产包，每个 ref 只取最新一版。

    带 org_id 查：跨租户拿到别人的角色档案，等于把别人的项目内容
    拼进自己的提示词里。
    """
    rows = list(
        (
            await db.execute(
                select(CharacterProfile)
                .where(
                    CharacterProfile.org_id == org_id,
                    CharacterProfile.project_id == project_id,
                    CharacterProfile.deleted_at.is_(None),
                )
                .order_by(CharacterProfile.ref, CharacterProfile.version.desc())
            )
        ).scalars()
    )
    latest: dict[str, CharacterProfile] = {}
    for row in rows:
        latest.setdefault(row.ref, row)
    return list(latest.values())


async def sync_from_characters_output(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    output: dict[str, Any],
) -> tuple[StyleProfile, list[CharacterProfile]]:
    """把角色档案 Agent 的产出接进一致性引擎。

    这是 `visual.character.v1` 的产出（`CharacterSheets`）与本引擎之间
    唯一的接缝。`CharacterSheet` 的外貌字段本来就是照着 `_appearance()`
    的字段名和顺序设计的，所以这里不做任何字段翻译——一翻译就会有
    两份字段名，改一处忘一处。

    两个动作都幂等：风格已存在不覆盖，角色已冻结不覆盖。所以这个函数
    既能在角色阶段跑完时调，也能在出图前当补齐用（存量项目从来没落过库）。
    """
    style = await ensure_style(db, org_id=org_id, project_id=project_id)
    designs = [d for d in output.get("characters", []) if isinstance(d, dict)]
    profiles = await upsert_characters(db, org_id=org_id, project_id=project_id, designs=designs)
    return style, profiles


async def list_scenes(
    db: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> list[SceneProfile]:
    """项目下的场景资产包，每个 ref 只取最新一版。

    带 org_id 查，理由和 `list_characters` 一字不差：跨租户拿到别人的
    场景档案，等于把别人的项目内容拼进自己的提示词里。
    """
    rows = list(
        (
            await db.execute(
                select(SceneProfile)
                .where(
                    SceneProfile.org_id == org_id,
                    SceneProfile.project_id == project_id,
                    SceneProfile.deleted_at.is_(None),
                )
                .order_by(SceneProfile.ref, SceneProfile.version.desc())
            )
        ).scalars()
    )
    latest: dict[str, SceneProfile] = {}
    for row in rows:
        latest.setdefault(row.ref, row)
    return list(latest.values())


async def sync_from_scenes_output(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    output: dict[str, Any],
) -> tuple[StyleProfile, list[SceneProfile]]:
    """把场景档案 Agent 的产出接进一致性引擎。

    `visual.scene.v1`（`SceneSheets`）与本引擎之间唯一的接缝，形状与
    `sync_from_characters_output` 完全相同——包括同样不做字段翻译：
    `SceneSheet` 的字段名就是 `_spatial()` 取的那些，一翻译就会有两份
    字段名，改一处忘一处。

    风格也在这里 `ensure_style`：正常流程里角色阶段已经建过了，这一步
    是幂等的空转；但场景阶段被单独重跑、或者存量项目从角色那一步之前
    就断了的情况下，它保证出场景参考图时一定有风格词可注入。
    """
    style = await ensure_style(db, org_id=org_id, project_id=project_id)
    designs = [d for d in output.get("scenes", []) if isinstance(d, dict)]
    profiles = await upsert_scenes(db, org_id=org_id, project_id=project_id, designs=designs)
    return style, profiles


async def upsert_scenes(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    designs: list[dict[str, Any]],
) -> list[SceneProfile]:
    """把 Visual Agent 产出的场景设定落成资产包。

    已冻结的场景不覆盖：同一场景的后续镜头都以它的摄影主轴和固定参照物
    为基准，中途改掉等于让同一个房间在前后两镜里变成两个房间。
    """
    out: list[SceneProfile] = []
    for design in designs:
        ref = str(design.get("ref", "")).strip()
        if not ref:
            continue

        existing = (
            await db.execute(
                select(SceneProfile)
                .where(
                    SceneProfile.project_id == project_id,
                    SceneProfile.ref == ref,
                    SceneProfile.deleted_at.is_(None),
                )
                .order_by(SceneProfile.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        if existing is not None:
            if existing.locked_at is None:
                existing.spatial_json = _spatial(design)
                existing.name = str(design.get("name", existing.name))
            out.append(existing)
            continue

        row = SceneProfile(
            org_id=org_id,
            project_id=project_id,
            ref=ref,
            name=str(design.get("name", ref)),
            spatial_json=_spatial(design),
        )
        db.add(row)
        await db.flush()
        out.append(row)

    return out


def _spatial(design: dict[str, Any]) -> dict[str, Any]:
    """只取结构化空间字段，丢掉其余。

    `camera_axis` / `fixed_references` / `lighting_states` 是场景一致性的全部
    依据，比 setting 那段散文重要得多——它们必须是可拼装的字段，所以这里
    逐字段取，不整份塞进去。

    锚点与光照的形状归一化直接复用 `agents.schemas` 里的两个纯函数，**不在
    这里再写一份**：它们回答的是"这份产出的合法形状是什么"，那是 schema 层的
    问题。抄一份过来就会有两套判断，改一处忘一处——本文件顶上"同样不做字段
    翻译"那条注释防的就是这件事。

    迁移已经把库里的存量数据改成结构化形状；复用这两个函数同时让**运行时**
    也扛得住扁平形状（备份恢复、模型照着旧样例输出），代价只是一次纯函数调用。
    """
    axis = design.get("camera_axis")
    axis = axis if isinstance(axis, dict) else {}
    lighting_states, default_lighting = agent_schemas.coerce_lighting_states(design)
    return {
        "time_slot": str(design.get("time_slot", "")),
        "setting": str(design.get("setting", "")),
        "lighting_states": lighting_states,
        "default_lighting": default_lighting,
        "camera_axis": {
            "position": str(axis.get("position", "")),
            "facing": str(axis.get("facing", "")),
            "far_end": str(axis.get("far_end", "")),
        },
        "fixed_references": agent_schemas.coerce_fixed_references(design.get("fixed_references")),
        "key_elements": [str(e) for e in design.get("key_elements", []) or []],
    }


async def lock_style(db: AsyncSession, style: StyleProfile) -> None:
    if style.locked_at is None:
        style.locked_at = datetime.now(UTC)
        log.info("consistency.style_locked", style_id=str(style.id))


async def upsert_characters(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    designs: list[dict[str, Any]],
) -> list[CharacterProfile]:
    """把 Visual Agent 产出的角色设定落成资产包。

    已冻结的角色不覆盖：后续镜头都以它为基准，
    中途改掉等于让前后镜头的主角变成两个人。
    """
    out: list[CharacterProfile] = []
    for design in designs:
        ref = str(design.get("ref", "")).strip()
        if not ref:
            continue

        existing = (
            await db.execute(
                select(CharacterProfile)
                .where(
                    CharacterProfile.project_id == project_id,
                    CharacterProfile.ref == ref,
                    CharacterProfile.deleted_at.is_(None),
                )
                .order_by(CharacterProfile.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        if existing is not None:
            if existing.locked_at is None:
                existing.appearance_json = _appearance(design)
                existing.name = str(design.get("name", existing.name))
            out.append(existing)
            continue

        row = CharacterProfile(
            org_id=org_id,
            project_id=project_id,
            ref=ref,
            name=str(design.get("name", ref)),
            appearance_json=_appearance(design),
        )
        db.add(row)
        await db.flush()
        out.append(row)

    return out


# 受控词表字段（`agents/schemas.py` 的 HEIGHT_BANDS / BODY_TYPES / POSTURES）。
# 顺序固定："多高 → 什么体型 → 什么体态"，与 describe_character 里其余字段
# 一样，顺序一变模型的注意力分布就变，出来的人就跟着变。
_BAND_KEYS = ("height", "body_type", "posture")


def _appearance(design: dict[str, Any]) -> dict[str, Any]:
    # 只取结构化外貌字段，丢掉其余——外貌必须是可拼装的字段，
    # 不是一段自由文本
    keys = ("age_range", "hair", "eyes", "face", "build", "outfit", "distinctive")
    out: dict[str, Any] = {k: str(design.get(k, "")) for k in keys}

    # 身高/体型/体态三个受控词表**优先于**自由文本的 build（ADR-037）。
    # 两者都存：`build` 是 describe_character 一直在读的那一列，形状不变；
    # 三个分列是给后续结构化比对用的——`reference_embedding` 至今为空的
    # 根因之一就是没有可比的结构化描述，一段自由文本没法做维度对齐。
    bands = {k: str(design.get(k, "")).strip() for k in _BAND_KEYS}
    out.update(bands)
    if composed := "，".join(v for v in (bands[k] for k in _BAND_KEYS) if v):
        out["build"] = composed
    return out


async def record_conditioning(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    shot_index: int,
    composed: compose.Composed,
    style_id: uuid.UUID,
    attempt: int,
    asset_id: uuid.UUID | None = None,
) -> ShotConditioning:
    """落库这一镜实际用了什么。

    `resolved_prompt` 存全文。没有它，任何"为什么这镜崩了"
    的问题都查不清，也没法复现。
    """
    row = ShotConditioning(
        org_id=org_id,
        project_id=project_id,
        shot_index=shot_index,
        style_profile_id=style_id,
        character_profile_ids=[uuid.UUID(c) for c in composed.character_ids],
        scene_profile_id=uuid.UUID(composed.scene_id) if composed.scene_id else None,
        resolved_prompt=composed.prompt,
        negative_prompt=composed.negative_prompt,
        seed=composed.seed,
        attempt=attempt,
        asset_id=asset_id,
    )
    db.add(row)
    await db.flush()
    return row


async def score_shot(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    shot_index: int,
    asset_id: uuid.UUID | None,
    reference_embedding: list[float],
    image_url: str,
) -> metrics.Similarity:
    """量角色相似度并落库。"""
    embedding = await metrics.embed_image_url(image_url)
    value = metrics.cosine(reference_embedding, embedding)
    verdict = metrics.judge_similarity(value)

    db.add(
        ShotQualityScore(
            org_id=org_id,
            project_id=project_id,
            shot_index=shot_index,
            asset_id=asset_id,
            metric="character_similarity",
            value=round(value, 4),
            threshold=metrics.CHARACTER_SIMILARITY_PASS,
            passed=verdict.passed,
        )
    )
    await db.flush()
    return verdict
