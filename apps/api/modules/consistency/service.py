"""一致性引擎的唯一对外入口。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.errors import AppError
from apps.api.core.logging import get_logger
from apps.api.modules.consistency import compose, metrics
from apps.api.modules.consistency.models import (
    CharacterProfile,
    ShotConditioning,
    ShotQualityScore,
    StyleProfile,
)

log = get_logger(__name__)

# 内置风格预设。用户可选，选定后冻结。
# 这些词由平台维护，不来自 Agent——见 compose.py 的说明。
STYLE_PRESETS: dict[str, dict[str, str]] = {
    "anime_suspense": {
        "base_model": "wan2.2-t2i-flash",
        "positive_tokens": "日式动画风格，赛璐璐上色，清晰线稿",
        "negative_tokens": "真人照片，3D渲染，模糊，多余手指，畸变，水印，文字",
        "color_grading": "低饱和，冷调，高对比",
        "line_weight": "中等线宽",
        "render_mode": "赛璐璐",
    },
    "ink_wash": {
        "base_model": "wan2.2-t2i-flash",
        "positive_tokens": "水墨风格，留白构图，淡彩",
        "negative_tokens": "真人照片，霓虹色，过曝，水印，文字",
        "color_grading": "低饱和，暖灰调",
        "line_weight": "细线",
        "render_mode": "水墨",
    },
}


async def ensure_style(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    preset: str = "anime_suspense",
    seed_base: int = 100_000,
) -> StyleProfile:
    """取项目的风格档案，没有就按预设创建。

    已存在就直接返回**不覆盖**——风格一旦有镜头产出即冻结，
    静默改掉会让前后镜头画风不一致且无从察觉。
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

    spec = STYLE_PRESETS.get(preset)
    if spec is None:
        raise AppError("provider.params.invalid", message=f"未知风格预设 {preset}")

    row = StyleProfile(org_id=org_id, project_id=project_id, seed_base=seed_base, **spec)
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


def _appearance(design: dict[str, Any]) -> dict[str, Any]:
    # 只取结构化外貌字段，丢掉其余——外貌必须是可拼装的字段，
    # 不是一段自由文本
    keys = ("age_range", "hair", "eyes", "face", "build", "outfit", "distinctive")
    return {k: str(design.get(k, "")) for k in keys}


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
