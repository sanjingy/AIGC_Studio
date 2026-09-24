"""BYOK / Skill 上传的数据层（ADR-024 ~ ADR-026）。

只验数据层：表建出来了、字段类型对得上、约束真的生效。
"迁移跑通了" 不等于 "表能用"——迁移只保证 DDL 没报错。

service / API / UI 是后续两个 Task 的事，这里一概不碰。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.crypto import decrypt_secret, encrypt_secret
from apps.api.core.db import session_scope
from apps.api.modules.billing.models import ProviderCredential
from apps.api.modules.skill.models import OrgSkill

pytestmark = pytest.mark.integration

FAKE_KEY = "sk-fake-key-for-test-0123456789"


async def test_provider_credential_round_trips_through_db(db: AsyncSession) -> None:
    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    row = ProviderCredential(
        org_id=org_id,
        capability="text_generation",
        provider_id="deepseek",
        key_encrypted=encrypt_secret(FAKE_KEY),
        created_by=user_id,
    )
    db.add(row)
    await db.commit()

    got = (
        await db.execute(select(ProviderCredential).where(ProviderCredential.org_id == org_id))
    ).scalar_one()
    assert got.id is not None
    assert got.created_at is not None and got.updated_at is not None
    assert got.deleted_at is None
    assert got.provider_id == "deepseek"
    assert got.created_by == user_id
    # 库里存的必须是密文，且能解回明文
    assert FAKE_KEY not in got.key_encrypted
    assert decrypt_secret(got.key_encrypted) == FAKE_KEY


async def test_one_key_per_org_capability() -> None:
    """一个 org 在同一个 capability 上，**同一家**只挂一把 Key。

    第二把必须被数据库拦住——本轮不做同一家多把轮换，靠约束把这条钉死，
    否则以后总会有某条新路径悄悄插进第二行。同能力下**另一家**的 Key
    是允许的（迁移 3a9d2c7e5b10，同能力多 Provider），见下面最后一段。
    """
    org_id = uuid.uuid4()

    async def _add(session: AsyncSession) -> None:
        session.add(
            ProviderCredential(
                org_id=org_id,
                capability="image_generation",
                provider_id="dashscope",
                key_encrypted=encrypt_secret(FAKE_KEY),
                created_by=uuid.uuid4(),
            )
        )

    async with session_scope() as s:
        await _add(s)

    with pytest.raises(IntegrityError):
        async with session_scope() as s:
            await _add(s)

    # 同一个 capability 换一家则不冲突
    async with session_scope() as s:
        s.add(
            ProviderCredential(
                org_id=org_id,
                capability="image_generation",
                provider_id="another-image-vendor",
                key_encrypted=encrypt_secret(FAKE_KEY),
                created_by=uuid.uuid4(),
            )
        )

    # 换一个 capability 则不冲突
    async with session_scope() as s:
        s.add(
            ProviderCredential(
                org_id=org_id,
                capability="text_generation",
                provider_id="deepseek",
                key_encrypted=encrypt_secret(FAKE_KEY),
                created_by=uuid.uuid4(),
            )
        )


async def test_org_skill_persists_spec_and_validation_result(db: AsyncSession) -> None:
    org_id = uuid.uuid4()
    yaml_text = "id: skill.demo.v1\nname: demo\nversion: v1\n"
    db.add(
        OrgSkill(
            org_id=org_id,
            name="demo",
            version="v1",
            spec_yaml=yaml_text,
            status="invalid",
            validation_errors="handler 不在白名单内",
            uploaded_by=uuid.uuid4(),
        )
    )
    await db.commit()

    got = (await db.execute(select(OrgSkill).where(OrgSkill.org_id == org_id))).scalar_one()
    assert got.spec_yaml == yaml_text  # 原文原样保存，重新校验要用
    assert got.status == "invalid"
    assert got.validation_errors == "handler 不在白名单内"


async def test_project_gets_skill_and_model_preference_columns(db: AsyncSession) -> None:
    """新增两列的类型与可空性。

    直接问库而不是问 ORM：ORM 里写成什么样不代表迁移建出了什么。
    """
    rows = (
        await db.execute(
            text(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_name = 'projects' "
                "AND column_name IN ('selected_skill_id', 'model_preference')"
            )
        )
    ).all()
    cols = {r[0]: (r[1], r[2]) for r in rows}
    assert cols["selected_skill_id"] == ("uuid", "YES")
    assert cols["model_preference"] == ("jsonb", "YES")


async def test_project_model_preference_accepts_capability_map(db: AsyncSession) -> None:
    """ADR-024 的覆盖表：capability → model_id，写进去读出来还是它。"""
    from apps.api.modules.project.models import Project

    org_id, skill_id = uuid.uuid4(), uuid.uuid4()
    pref = {"text_generation": "deepseek-chat", "image_generation": "wan2.2-t2i-plus"}
    db.add(
        Project(
            org_id=org_id,
            owner_user_id=uuid.uuid4(),
            title="BYOK schema 用例",
            current_state_json={},
            selected_skill_id=skill_id,
            model_preference=pref,
        )
    )
    await db.commit()

    got = (await db.execute(select(Project).where(Project.org_id == org_id))).scalar_one()
    assert got.selected_skill_id == skill_id
    assert got.model_preference == pref


async def test_project_model_preference_defaults_to_empty(db: AsyncSession) -> None:
    """不传时是空字典，不是 None——读侧仍应写 `... or {}` 兜住存量 NULL 行。"""
    from apps.api.modules.project.models import Project

    org_id = uuid.uuid4()
    db.add(
        Project(
            org_id=org_id,
            owner_user_id=uuid.uuid4(),
            title="默认值用例",
            current_state_json={},
        )
    )
    await db.commit()

    got = (await db.execute(select(Project).where(Project.org_id == org_id))).scalar_one()
    assert got.model_preference == {}
    assert got.selected_skill_id is None
