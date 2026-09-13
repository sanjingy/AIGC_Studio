"""成品提示词与生成记录的端点。

两个路由挂在同一个模块下，因为它们是同一件事的两半：一半是"这次要用什么
词去生成"，另一半是"上次到底用了什么词生成的"。分成两个模块会让
`rule_version` / `basis_digest` 这几个键在两边各写一遍，然后慢慢分叉。

**GET 一律零推理。** 读提示词、判过期、翻记录都不调模型；只有用户显式
POST 一次"准备提示词"或者点生成，才会有新的推理发生（并因此花钱）。
"""

from __future__ import annotations

import uuid
from dataclasses import asdict

from fastapi import APIRouter, Body, Path, Query, status

from apps.api.modules.auth.deps import CurrentUser, DbSession
from apps.api.modules.prompting import records as records_mod
from apps.api.modules.prompting import service as prompting
from apps.api.modules.prompting.schemas import (
    GenerationRecordDetail,
    GenerationRecordStep,
    GenerationRecordSummary,
    PromptIn,
    PromptOut,
)

router = APIRouter(prefix="/projects/{project_id}/prompts", tags=["prompts"])
records_router = APIRouter(prefix="/projects/{project_id}/generation-records", tags=["records"])


def _out(prompt: prompting.Prompt) -> PromptOut:
    return PromptOut(
        run_id=str(prompt.run_id),
        kind=prompt.kind,
        subject_key=prompt.subject_key,
        prompt=prompt.prompt,
        negative_prompt=prompt.negative_prompt,
        basis_digest=prompt.basis_digest,
        stale=prompt.stale,
        rule_version=prompt.rule_version,
        agent_id=prompt.agent_id,
        model_id=prompt.model_id,
        created_at=prompt.created_at,
    )


@router.get("/{kind}/{subject_key}", response_model=PromptOut | None)
async def get_prompt(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    kind: str = Path(min_length=1, max_length=20),
    subject_key: str = Path(min_length=1, max_length=40),
) -> PromptOut | None:
    """看这个对象当前的提示词。**只读，不调模型，一分钱不花。**

    还没准备过就返回 null，不代生成——代生成会让"点开看一眼"变成一次
    计费动作，而用户点开的意图只是想知道有没有。
    """
    prompt = await prompting.latest(
        db, org_id=user.org_id, project_id=project_id, kind=kind, subject_key=subject_key
    )
    return _out(prompt) if prompt is not None else None


@router.post(
    "/{kind}/{subject_key}", response_model=PromptOut, status_code=status.HTTP_201_CREATED
)
async def prepare_prompt(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    payload: PromptIn | None = Body(None),
    kind: str = Path(min_length=1, max_length=20),
    subject_key: str = Path(min_length=1, max_length=40),
) -> PromptOut:
    """准备一份提示词。**会真的调一次模型。**

    同样的上下文与同样的创作要求已经有一份合格产出时直接复用，不重复花钱。
    风格词缺失或被改写、前置数据不全时报错并**不留下一份可用的提示词**——
    不退回旧的确定性拼接兜底（ADR-036）。
    """
    prompt = await prompting.prepare(
        db,
        org_id=user.org_id,
        project_id=project_id,
        kind=kind,
        subject_key=subject_key,
        instruction=(payload.instruction if payload else ""),
    )
    return _out(prompt)


@records_router.get("", response_model=list[GenerationRecordSummary])
async def list_generation_records(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    limit: int = Query(50, ge=1, le=records_mod.MAX_RECORDS),
) -> list[GenerationRecordSummary]:
    """项目里的生成记录，新的在前。**列表不含全文。**"""
    rows = await records_mod.list_records(
        db, org_id=user.org_id, project_id=project_id, limit=limit
    )
    return [GenerationRecordSummary(**asdict(r)) for r in rows]


@records_router.get("/{record_type}/{record_id}", response_model=GenerationRecordDetail)
async def get_generation_record(
    project_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    record_id: uuid.UUID,
    record_type: str = Path(min_length=1, max_length=16),
) -> GenerationRecordDetail:
    """一条记录的全文详情。**不调模型。**"""
    detail = await records_mod.get_record(
        db,
        org_id=user.org_id,
        project_id=project_id,
        record_type=record_type,
        record_id=record_id,
    )
    return GenerationRecordDetail(
        **asdict(detail.summary),
        user_input=detail.user_input,
        prompt=detail.prompt,
        negative_prompt=detail.negative_prompt,
        actual_prompt=detail.actual_prompt,
        rule_version=detail.rule_version,
        basis_digest=detail.basis_digest,
        incomplete=detail.incomplete,
        steps=[GenerationRecordStep(**asdict(s)) for s in detail.steps],
        output=detail.output,
    )
