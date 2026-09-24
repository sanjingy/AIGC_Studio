"""模型上游配置：组织默认、自定义端点，以及"这次该调谁"的唯一判定（FR-GW-005 / FR-GW-020）。

**三层取值顺序**（05_MODEL_GATEWAY.md §6.1，逐层兜底）：

    项目选择（projects.model_preference）> 组织默认（org_model_defaults）> 平台目录默认

**判定只有一份**：:func:`decide` 是纯函数，Gateway 的调用解析
（`service._resolve`）与计费的 BYOK 判断（`billing.pricing.uses_own_key`）
都从它拿结论。计费和调用各算各的，就会出现"按折扣价扣钱、却拿平台 Key
去调"——这个仓库已经这样倒贴过一次（ADR-027 的起因）。

**计费来源是显式的**：组织默认里存着用户选的 `key_source`
（平台额度 / 这家的自有 Key），不再从"库里有没有这把 Key"推断。
只有**没有组织默认、或组织默认选的是另一家**时，才退回旧规则
（有 Key 就用 Key）——那是迁移之前所有租户的行为，老数据按它继续工作。

**Provider 与模型不匹配一律拒绝，不静默换一家**：组织默认存的模型必须属于
它选的那家；项目偏好指向的模型反查出来是哪家就用哪家；组织默认指向一家
已经下线的 Provider 时报错，而不是悄悄落到平台默认。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from adapters.providers import endpoint_url
from adapters.providers.base import KeySource
from adapters.providers.openai_compat import OpenAICompatTextProvider
from apps.api.core.crypto import CryptoConfigError, decrypt_secret, encrypt_secret
from apps.api.core.errors import AppError
from apps.api.core.logging import get_logger
from apps.api.modules.billing import credentials
from apps.api.modules.gateway import catalog, probe
from apps.api.modules.gateway import repository as repo
from apps.api.modules.gateway.models import KEY_SOURCES

log = get_logger(__name__)

Layer = Literal["project", "org", "platform"]

MAX_LABEL_CHARS = 64
MAX_MODEL_ID_CHARS = 128


# ---------------------------------------------------------------- 判定（纯函数）


@dataclass(frozen=True, slots=True)
class DefaultRow:
    """组织默认的值。不是 ORM 对象：判定函数不该持有会话。"""

    provider_id: str
    model_id: str | None
    key_source: str


@dataclass(frozen=True, slots=True)
class Decision:
    """这次调用选中的上游。

    `provider_id=None` 只在"项目偏好是一个目录里查不到 Provider 的旧模型 id"
    时出现——那时按旧行为把它当成重排提示交给路由表，匹配不上就原样走默认顺序。
    `key_source=None` 表示组织没有为这家显式选过计费来源，由调用方按旧规则
    （有没有这家的 Key）决定。
    """

    capability: str
    provider_id: str | None
    model_id: str | None
    layer: Layer
    key_source: KeySource | None


def _retired(capability: str, provider_id: str) -> AppError:
    return AppError(
        "provider.byok.rejected",
        message=(
            f"组织为 {capability} 选的上游 {provider_id} 已不再提供该能力，请到模型页重新选择"
        ),
        detail={"capability": capability, "reason": "provider_retired", "provider_id": provider_id},
    )


def _provider_exists(capability: str, provider_id: str) -> bool:
    if provider_id == catalog.CUSTOM_TEXT_PROVIDER_ID:
        return catalog.supports_custom_endpoint(capability)
    return any(spec.provider_id == provider_id for spec in catalog.providers_for(capability))


def decide(capability: str, *, preference: str | None, default: DefaultRow | None) -> Decision:
    """按三层顺序选出上游。不查库、不解密，给什么算什么。"""
    explicit: KeySource | None = None
    if default is not None and default.key_source in KEY_SOURCES:
        explicit = KeySource(default.key_source)

    def key_for(provider_id: str) -> KeySource | None:
        if provider_id == catalog.CUSTOM_TEXT_PROVIDER_ID:
            return KeySource.ORG  # 自定义端点只可能是用户自己的 Key
        if default is not None and default.provider_id == provider_id:
            return explicit
        return None

    if preference:
        if preference == catalog.CUSTOM_TEXT_PROVIDER_ID and catalog.supports_custom_endpoint(
            capability
        ):
            return Decision(capability, preference, None, "project", KeySource.ORG)
        spec = catalog.provider_for_model(capability, preference)
        if spec is not None:
            return Decision(
                capability, spec.provider_id, preference, "project", key_for(spec.provider_id)
            )
        # 查不到这家：旧偏好 / 目录改过 / 模型下线。按"偏好过期"处理，
        # 往下一层走，不因为一条陈旧的偏好把整个能力停掉（`gateway.preference_stale`）。
        log.info("upstreams.preference_stale", capability=capability, model_id=preference)
        if default is None:
            # 没有组织默认时保留接入组织默认之前的旧行为：把它当成路由表上的
            # 重排提示，匹配得上就排前面，匹配不上原样走默认顺序。
            return Decision(capability, None, preference, "project", None)

    if default is not None:
        if not _provider_exists(capability, default.provider_id):
            raise _retired(capability, default.provider_id)
        return Decision(
            capability, default.provider_id, default.model_id, "org", key_for(default.provider_id)
        )

    spec = catalog.default_provider(capability)
    return Decision(capability, spec.provider_id if spec else None, None, "platform", None)


# ---------------------------------------------------------------- 读库


async def load_default(
    db: AsyncSession, *, org_id: uuid.UUID, capability: str
) -> DefaultRow | None:
    row = await repo.get_default(db, org_id=org_id, capability=capability)
    if row is None:
        return None
    return DefaultRow(provider_id=row.provider_id, model_id=row.model_id, key_source=row.key_source)


@dataclass(frozen=True, slots=True)
class ResolvedEndpoint:
    """一次调用要用的自定义端点。**`api_key` 是明文，只活到这次请求结束。**"""

    label: str
    base_url: str
    model_id: str
    api_key: str


async def load_endpoint(db: AsyncSession, *, org_id: uuid.UUID) -> ResolvedEndpoint | None:
    row = await repo.get_endpoint(db, org_id=org_id)
    if row is None:
        return None
    try:
        key = decrypt_secret(row.key_encrypted)
    except CryptoConfigError as exc:
        log.warning("upstreams.endpoint_decrypt_failed", org_id=str(org_id))
        raise AppError(
            "provider.byok.rejected",
            message=f"自定义端点的 Key 无法解密：{exc}。请重新填写一次。",
            detail={"capability": catalog.CUSTOM_TEXT_CAPABILITY, "reason": "decrypt_failed"},
        ) from exc
    return ResolvedEndpoint(
        label=row.label, base_url=row.base_url, model_id=row.model_id, api_key=key
    )


async def effective_key_source(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    capability: str,
    project_preference: str | None = None,
) -> KeySource:
    """计费问的那一句：这次会用谁的 Key。与 Gateway 的 `_resolve` 同一个 :func:`decide`。"""
    decision = decide(
        capability,
        preference=project_preference,
        default=await load_default(db, org_id=org_id, capability=capability),
    )
    if decision.key_source is not None:
        return decision.key_source
    provider_id = decision.provider_id or catalog.provider_of().get(capability)
    if provider_id is None:
        return KeySource.PLATFORM
    own = await credentials.has_own_key(
        db, org_id=org_id, capability=capability, provider_id=provider_id
    )
    return KeySource.ORG if own else KeySource.PLATFORM


# ---------------------------------------------------------------- 配置视图


@dataclass(frozen=True, slots=True)
class EndpointView:
    """自定义端点的展示态。**没有 Key 明文**，只有尾号。"""

    label: str
    base_url: str
    model_id: str
    masked_key: str | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SelectionView:
    provider_id: str | None
    model_id: str | None
    key_source: str
    #: `org` = 组织显式存过；`platform` = 没存过，走的是目录默认
    layer: Literal["org", "platform"]
    updated_at: datetime | None


async def endpoint_view(db: AsyncSession, *, org_id: uuid.UUID) -> EndpointView | None:
    row = await repo.get_endpoint(db, org_id=org_id)
    if row is None:
        return None
    try:
        masked: str | None = credentials.mask(decrypt_secret(row.key_encrypted))
    except CryptoConfigError:
        masked = None
    return EndpointView(
        label=row.label,
        base_url=row.base_url,
        model_id=row.model_id,
        masked_key=masked,
        updated_at=row.updated_at,
    )


async def selection_view(db: AsyncSession, *, org_id: uuid.UUID, capability: str) -> SelectionView:
    """组织这一层现在是什么。没存过就如实给出"平台默认"及它实际会用的计费来源。"""
    row = await repo.get_default(db, org_id=org_id, capability=capability)
    if row is not None:
        return SelectionView(
            provider_id=row.provider_id,
            model_id=row.model_id,
            key_source=row.key_source,
            layer="org",
            updated_at=row.updated_at,
        )
    source = await effective_key_source(db, org_id=org_id, capability=capability)
    spec = catalog.default_provider(capability)
    return SelectionView(
        provider_id=spec.provider_id if spec else None,
        model_id=None,
        key_source=source.value,
        layer="platform",
        updated_at=None,
    )


# ---------------------------------------------------------------- 写：组织默认


def _require_selectable(capability: str) -> None:
    if capability not in credentials.configurable_capabilities():
        raise AppError(
            "common.validation_failed",
            message=f"能力 {capability!r} 还不能选择上游",
            detail={"allowed": list(credentials.configurable_capabilities())},
        )


async def set_default(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    capability: str,
    provider_id: str,
    model_id: str | None,
    key_source: str,
) -> SelectionView:
    """保存组织默认。**不合法的组合在这里拒绝，不存一条调用时才会炸的配置。**

    - 上游必须是这个能力下真有适配器的一家，或（仅文本）自定义端点；
    - 模型必须属于这家——A 家的模型名配 B 家的 Key 是拒绝，不是"帮你换一家"；
    - 自定义端点的模型就是端点上填的那个，这里不接受另一个名字，计费来源只能是自有；
    - 选"自有 Key"时这家必须已经存了 Key，否则第一次生成才报错，用户会以为
      是生成出了问题。
    """
    _require_selectable(capability)
    if key_source not in KEY_SOURCES:
        raise AppError(
            "common.validation_failed",
            message="计费来源只能是 platform 或 org",
            detail={"allowed": list(KEY_SOURCES)},
        )

    if provider_id == catalog.CUSTOM_TEXT_PROVIDER_ID:
        if not catalog.supports_custom_endpoint(capability):
            raise AppError(
                "provider.params.invalid",
                message=f"{capability} 不支持自定义端点（只有文本生成支持）",
                detail={"capability": capability},
            )
        endpoint = await repo.get_endpoint(db, org_id=org_id)
        if endpoint is None:
            raise AppError(
                "common.validation_failed",
                message="还没有保存自定义端点，先填写地址、模型与 Key",
            )
        if model_id is not None and model_id != endpoint.model_id:
            raise AppError(
                "provider.params.invalid",
                message=f"自定义端点配置的模型是 {endpoint.model_id!r}，不是 {model_id!r}",
            )
        if key_source != KeySource.ORG.value:
            raise AppError(
                "common.validation_failed",
                message="自定义端点只能用你自己的 Key 计费",
            )
        model_id = None  # 端点的模型随端点走，改端点时不用再改一遍默认
    else:
        allowed_providers = [spec.provider_id for spec in catalog.providers_for(capability)]
        if provider_id not in allowed_providers:
            raise AppError(
                "provider.params.invalid",
                message=f"{capability} 下没有接入 Provider {provider_id!r}",
                detail={"allowed": allowed_providers},
            )
        if model_id is not None and model_id not in catalog.model_ids(capability, provider_id):
            raise AppError(
                "provider.params.invalid",
                message=f"模型 {model_id!r} 不属于 {catalog.provider_label(provider_id)}",
                detail={"allowed": list(catalog.model_ids(capability, provider_id))},
            )
        if key_source == KeySource.ORG.value and not await credentials.has_own_key(
            db, org_id=org_id, capability=capability, provider_id=provider_id
        ):
            raise AppError(
                "common.validation_failed",
                message=(
                    f"还没有保存 {catalog.provider_label(provider_id)} 的 Key，"
                    "先配置密钥再选自有账号计费"
                ),
            )

    await repo.upsert_default(
        db,
        org_id=org_id,
        capability=capability,
        provider_id=provider_id,
        model_id=model_id,
        key_source=key_source,
        updated_by=user_id,
    )
    await db.commit()
    log.info(
        "upstreams.default_saved",
        org_id=str(org_id),
        capability=capability,
        provider=provider_id,
        model=model_id,
        key_source=key_source,
    )
    return await selection_view(db, org_id=org_id, capability=capability)


async def on_key_removed(
    db: AsyncSession, *, org_id: uuid.UUID, capability: str, provider_id: str
) -> None:
    """某家的 Key 被移除后，把"用这家自有 Key 计费"的组织默认改回平台额度。

    不改的话，下一次生成会因为"选了自有 Key 却没有 Key"失败；而移除 Key
    这个动作一直以来的承诺是"该能力自动退回平台档"。**不 commit**，
    与移除 Key 在同一个事务里提交。
    """
    row = await repo.get_default(db, org_id=org_id, capability=capability)
    if row is None or row.provider_id != provider_id or row.key_source != KeySource.ORG.value:
        return
    await repo.upsert_default(
        db,
        org_id=org_id,
        capability=capability,
        provider_id=row.provider_id,
        model_id=row.model_id,
        key_source=KeySource.PLATFORM.value,
        updated_by=row.updated_by,
    )


# ---------------------------------------------------------------- 写：自定义端点


def _clean_label(label: str) -> str:
    text = (label or "").strip()
    if not text:
        raise AppError(
            "common.validation_failed", message="请填写供应商名称", detail={"field": "label"}
        )
    if len(text) > MAX_LABEL_CHARS or not text.isprintable():
        raise AppError(
            "common.validation_failed",
            message=f"供应商名称最长 {MAX_LABEL_CHARS} 个字，且不能含控制字符",
            detail={"field": "label"},
        )
    return text


def _clean_model_id(model_id: str) -> str:
    text = (model_id or "").strip()
    if not text:
        raise AppError(
            "common.validation_failed", message="请填写模型 ID", detail={"field": "model_id"}
        )
    if len(text) > MAX_MODEL_ID_CHARS or any(ch.isspace() for ch in text) or not text.isprintable():
        raise AppError(
            "common.validation_failed",
            message=f"模型 ID 最长 {MAX_MODEL_ID_CHARS} 个字符，且不能含空白",
            detail={"field": "model_id"},
        )
    return text


async def save_endpoint(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    label: str,
    base_url: str,
    model_id: str,
    api_key: str | None,
) -> EndpointView:
    """新建或更新自定义端点。已有端点时 `api_key` 可以不给（沿用原来那把）。"""
    clean_label = _clean_label(label)
    clean_url = endpoint_url.normalize_base_url(base_url)
    clean_model = _clean_model_id(model_id)
    existing = await repo.get_endpoint(db, org_id=org_id)
    if api_key is None and existing is None:
        raise AppError(
            "common.validation_failed", message="请填写 API Key", detail={"field": "api_key"}
        )
    key_encrypted = encrypt_secret(credentials.clean_key(api_key)) if api_key is not None else None
    await repo.save_endpoint(
        db,
        org_id=org_id,
        label=clean_label,
        base_url=clean_url,
        model_id=clean_model,
        key_encrypted=key_encrypted,
        created_by=user_id,
    )
    await db.commit()
    # 日志里只有 host，没有 path（path 里偶尔有租户号）、更没有 Key
    log.info("upstreams.endpoint_saved", org_id=str(org_id), model=clean_model)
    view = await endpoint_view(db, org_id=org_id)
    assert view is not None
    return view


async def delete_endpoint(db: AsyncSession, *, org_id: uuid.UUID) -> None:
    """删除自定义端点。选它当组织默认的，一并退回平台目录默认。"""
    row = await repo.get_endpoint(db, org_id=org_id)
    if row is None:
        raise AppError("common.not_found", message="还没有配置自定义端点")
    await repo.soft_delete_endpoint(db, row=row)
    default = await repo.get_default(db, org_id=org_id, capability=catalog.CUSTOM_TEXT_CAPABILITY)
    if default is not None and default.provider_id == catalog.CUSTOM_TEXT_PROVIDER_ID:
        await repo.soft_delete_default(db, row=default)
    await db.commit()
    log.info("upstreams.endpoint_removed", org_id=str(org_id))


async def test_endpoint(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    base_url: str | None = None,
    model_id: str | None = None,
    api_key: str | None = None,
) -> probe.ProbeResult:
    """测试连接。没给的字段用已保存的值——Key 明文前端拿不回来，只能让后端自己解。

    与正式调用是**同一个适配器类**（`OpenAICompatTextProvider.verify_key`），
    同样的地址规整与出网校验。地址不合法照样返回"测试结论"而不是 4xx，
    与 `probe.verify` 的约定一致：这把配置不能用是一个正常的测试结果。
    """
    saved = (
        await load_endpoint(db, org_id=org_id) if None in (base_url, model_id, api_key) else None
    )
    url = base_url if base_url is not None else (saved.base_url if saved else None)
    model = model_id if model_id is not None else (saved.model_id if saved else None)
    key = (
        credentials.clean_key(api_key)
        if api_key is not None
        else (saved.api_key if saved else None)
    )
    if not url or not model or not key:
        raise AppError("common.not_found", message="还没有配置自定义端点，请先填写地址、模型与 Key")

    pid = catalog.CUSTOM_TEXT_PROVIDER_ID
    try:
        provider = OpenAICompatTextProvider(
            base_url=url, api_key=key, model_id=_clean_model_id(model), key_source=KeySource.ORG
        )
    except AppError as exc:
        return probe.ProbeResult(
            ok=False, provider_id=pid, message=exc.message, error_code=exc.code
        )
    return await probe.verify_with(provider, provider_id=pid, api_key=key)
