"""用户上传 Skill 的服务层（ADR-026）。

**范围**：能传、能看、能删。**不接运行时**——生产流程仍然走
`orchestrator._NEXT` 的硬编码阶段图，上传的 Skill 现在不参与任何生产。
这不是漏做，是 ADR-026 明确决定的，界面必须如实标注（`runtime_wired`
就是给前端标这句话用的），不能做成一个看起来能用、实际不生效的功能。

**校验直接复用 `skills/spec.py`**（04_SkillSpec.md §7 的白名单：处理器、
导出路径、门、能力、重试次数）。那套规则本来就是为不可信输入写的，
这里不新增任何豁免——内置 Skill 都不享受特权，用户上传的更不该有。

**上传不落 `skills/custom/`**。那个目录是进程级注册表，写进去等于对
**所有租户**生效——一个 org 传的东西会出现在别人的可选列表里。所以
用户上传的一律只进 `org_skills` 表，按 org_id 隔离；`skills/custom/`
留给运维手工投放的、确实要全局生效的 spec。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import yaml
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.errors import AppError
from apps.api.core.logging import get_logger
from apps.api.modules.skill import repository as repo
from apps.api.modules.skill.models import OrgSkill
from skills import registry as skill_registry
from skills.spec import SkillSpec, load_spec

log = get_logger(__name__)

# 与 registry 同一个上限，不复制常量——那边一改这边就跟着变
MAX_SPEC_BYTES = skill_registry.MAX_SPEC_BYTES

# 校验失败时 name 的兜底。坏 spec 也要入库让用户看见错在哪，
# 但列表里总得有个能认出"是我传的哪一个"的字符串。
_FALLBACK_NAME = "未命名 Skill"
_FALLBACK_VERSION = "-"

# 单条错误信息的长度上限。pydantic 对一个 40 阶段的 spec 能吐出很长的
# 报告，整条塞进列表接口会把响应撑爆——存原文的是 spec_yaml，
# 这里只要够定位问题。
MAX_ERROR_CHARS = 4000


@dataclass(frozen=True, slots=True)
class SkillView:
    """一条上传记录。**不含 spec 原文**——列表页不需要，也不该顺手发出去。"""

    id: uuid.UUID
    name: str
    version: str
    status: str
    validation_errors: str | None
    created_at: datetime

    # 下面这些是从 spec_yaml 现算的，没有对应的列：校验规则以后会变，
    # 冻成列就得写迁移去回填。
    skill_id: str | None
    route: str | None
    stage_count: int
    gates: list[str]
    #: spec 钉死引用、但当前 Agent 注册表里不存在的 id。
    #: 不影响 status——Agent 注册表是热加载的，缺一个不代表 spec 非法。
    missing_agents: list[str]


def _version_of(skill_id: str) -> str:
    """从 `skill.novel_to_anime.v1` 里取出 `v1`。

    id 的格式由 `_SKILL_ID_RE` 保证，走到这里时一定带 `.vN` 后缀。
    """
    return skill_id.rsplit(".", 1)[-1]


def _parse(raw_yaml: str) -> dict[str, object]:
    """把上传内容解析成映射。

    `safe_load` 不是可选项：`yaml.load` 能构造任意 Python 对象，
    对用户上传的文件用它等于远程代码执行。registry 里同一条理由。
    """
    try:
        data = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        raise AppError("skill.spec.unreadable", message=str(exc)[:500]) from exc
    if not isinstance(data, dict):
        raise AppError("skill.spec.unreadable", message="spec 顶层必须是映射")
    return data


def _explain(exc: Exception) -> str:
    """把校验异常压成给人看的一段文字。"""
    if isinstance(exc, ValidationError):
        lines = [
            f"{'.'.join(str(p) for p in e['loc']) or '(顶层)'}：{e['msg']}" for e in exc.errors()
        ]
        text = "\n".join(lines)
    else:
        text = f"{type(exc).__name__}: {exc}"
    return text[:MAX_ERROR_CHARS]


def _view(row: OrgSkill) -> SkillView:
    spec: SkillSpec | None = None
    if row.status == "valid":
        # 存的时候校验过一次，这里理论上不会失败；真失败了也只是少几个
        # 派生字段，不该让整个列表接口 500。
        try:
            spec = load_spec(_parse(row.spec_yaml), source="custom")
        except Exception:  # 派生信息缺失不是错误，降级即可
            log.warning("skill.reparse_failed", skill_row_id=str(row.id))

    return SkillView(
        id=row.id,
        name=row.name,
        version=row.version,
        status=row.status,
        validation_errors=row.validation_errors,
        created_at=row.created_at,
        skill_id=spec.id if spec else None,
        route=spec.route if spec else None,
        stage_count=len(spec.stages) if spec else 0,
        gates=spec.effective_gates() if spec else [],
        missing_agents=skill_registry.missing_agents(spec) if spec else [],
    )


async def upload(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    raw_yaml: str,
) -> SkillView:
    """收下一份 Skill YAML。

    **校验不通过也入库**（status=invalid，错误原文一起存）。丢掉它只会
    让用户得到一个"传了没反应"的黑洞——他需要看见自己哪里写错了。
    只有连 YAML 都解析不了才拒收，那种情况没有任何可展示的内容。
    """
    if len(raw_yaml.encode("utf-8")) > MAX_SPEC_BYTES:
        raise AppError(
            "skill.spec.too_large",
            message=f"spec 超过 {MAX_SPEC_BYTES} 字节上限",
        )

    raw = _parse(raw_yaml)

    name = _FALLBACK_NAME
    version = _FALLBACK_VERSION
    status = "invalid"
    errors: str | None = None

    try:
        spec = load_spec(raw, source="custom")
        name, version, status = spec.name, _version_of(spec.id), "valid"
    except Exception as exc:  # 任何校验失败都要变成给用户看的文字，不是 500
        errors = _explain(exc)
        # 名字尽量从原文里捞一个，让用户在列表里认得出是哪一份
        candidate = raw.get("name")
        if isinstance(candidate, str) and candidate.strip():
            name = candidate.strip()[:120]

    row = repo.add(
        db,
        OrgSkill(
            org_id=org_id,
            name=name,
            version=version,
            spec_yaml=raw_yaml,
            status=status,
            validation_errors=errors,
            uploaded_by=user_id,
        ),
    )
    # get_db 不自动提交（见 core/db.py），写路径自己提交
    await db.commit()

    log.info("skill.uploaded", skill_row_id=str(row.id), status=status)
    return _view(row)


async def list_for_org(db: AsyncSession, *, org_id: uuid.UUID) -> list[SkillView]:
    return [_view(r) for r in await repo.list_for_org(db, org_id=org_id)]


async def get_yaml(db: AsyncSession, *, org_id: uuid.UUID, skill_id: uuid.UUID) -> str:
    """取回原文，供用户在界面上核对自己传了什么。"""
    row = await repo.get(db, org_id=org_id, skill_id=skill_id)
    if row is None:
        # 跨租户一律 404 不 403——403 会确认资源存在，可被枚举
        raise AppError("common.not_found")
    return row.spec_yaml


async def delete(db: AsyncSession, *, org_id: uuid.UUID, skill_id: uuid.UUID) -> None:
    row = await repo.get(db, org_id=org_id, skill_id=skill_id)
    if row is None:
        raise AppError("common.not_found")
    row.deleted_at = datetime.now(UTC)
    await db.commit()
    log.info("skill.deleted", skill_row_id=str(skill_id))
