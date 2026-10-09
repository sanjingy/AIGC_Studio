"""模型上游配置：组织默认、组织供应商连接，以及"这次该调谁"的唯一判定（FR-GW-005 / FR-GW-020）。

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

**组织供应商连接（ADR-039）**：每个连接以虚拟路由 `provider.org:<连接 id>` 出现，
一律是用户自己的 Key（`KeySource.ORG`）。连接被删、被禁用、没有这个能力的模型、
选的模型不在连接里——任何一条都报 `provider.byok.rejected` 的可读错误，
不落平台、不换到别的连接。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from adapters.providers import endpoint_url
from adapters.providers.base import KeySource
from adapters.providers.model_discovery import normalize_api_base
from apps.api.core.config import get_settings
from apps.api.core.crypto import CryptoConfigError, decrypt_secret, encrypt_secret
from apps.api.core.errors import AppError
from apps.api.core.logging import get_logger
from apps.api.modules.billing import credentials
from apps.api.modules.gateway import catalog, presets, probe
from apps.api.modules.gateway import repository as repo
from apps.api.modules.gateway.models import KEY_SOURCES, OrgProviderConnection
from apps.api.modules.gateway.schemas import ConnectionDiscoverOut

log = get_logger(__name__)

Layer = Literal["project", "org", "platform"]

MAX_LABEL_CHARS = 64
MAX_MODEL_ID_CHARS = presets.MAX_MODEL_ID_CHARS
MAX_MODELS_PER_CONNECTION = 50


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
    #: 选中的是组织连接时为它的 id；前缀对但 id 坏了 / 旧 `provider.custom.text`
    #: 时 `provider_id` 仍是原串、这里为 None——解析时按"连接不存在"报错。
    connection_id: uuid.UUID | None = None

    @property
    def is_org_connection(self) -> bool:
        return self.provider_id is not None and catalog.parse_org_ref(self.provider_id) is not None


def _retired(capability: str, provider_id: str) -> AppError:
    return AppError(
        "provider.byok.rejected",
        message=(
            f"组织为 {capability} 选的上游 {provider_id} 已不再提供该能力，请到模型页重新选择"
        ),
        detail={"capability": capability, "reason": "provider_retired", "provider_id": provider_id},
    )


def _provider_exists(capability: str, provider_id: str) -> bool:
    if catalog.parse_org_ref(provider_id) is not None:
        return catalog.supports_org_connections(capability)
    return any(spec.provider_id == provider_id for spec in catalog.providers_for(capability))


def _org_decision(capability: str, ref: str, *, model_id: str | None, layer: Layer) -> Decision:
    parsed = catalog.parse_org_ref(ref)
    assert parsed is not None
    connection_id, ref_model = parsed
    return Decision(
        capability,
        catalog.org_provider_id(connection_id) if connection_id else ref,
        model_id if model_id is not None else ref_model,
        layer,
        KeySource.ORG,  # 组织连接只可能是用户自己的 Key
        connection_id,
    )


def decide(capability: str, *, preference: str | None, default: DefaultRow | None) -> Decision:
    """按三层顺序选出上游。不查库、不解密，给什么算什么。"""
    explicit: KeySource | None = None
    if default is not None and default.key_source in KEY_SOURCES:
        explicit = KeySource(default.key_source)

    def key_for(provider_id: str) -> KeySource | None:
        if default is not None and default.provider_id == provider_id:
            return explicit
        return None

    if preference:
        if catalog.parse_local_ref(preference) is not None:
            # 项目选的是本机会员 CLI（ADR-041）。走到 Gateway 说明本机那条没接走它
            # （部署关了试点、项目不在白名单）。**不许**按"偏好过期"往下落：
            # 那会把一次用户以为不花钱的调用悄悄变成平台或他自己 Key 的付费调用。
            raise AppError(
                "local_runtime.text_not_configured",
                message=f"project text source is {preference} but local runtime is not routing it",
                detail={"capability": capability, "reason": "local_selected"},
            )
        if catalog.parse_org_ref(preference) is not None:
            # 项目把这个能力指到了组织连接（`provider.org:<id>[:<模型>]`）。
            # 能力对不对得上要看连接里的协议，这里不查库，交给解析时校验。
            return _org_decision(capability, preference, model_id=None, layer="project")
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
        if catalog.parse_org_ref(default.provider_id) is not None:
            return _org_decision(
                capability, default.provider_id, model_id=default.model_id, layer="org"
            )
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
class ResolvedConnection:
    """一次调用要用的组织连接。**`api_key` 是明文，只活到这次请求结束。**"""

    connection_id: uuid.UUID
    label: str
    preset_id: str | None
    base_url: str
    models: tuple[tuple[str, str], ...]  # (model_id, protocol)
    enabled: bool
    api_key: str
    #: 用户标成"推理模型"的 (model_id, protocol)。`no_reasoning_roles` 的角色解析到
    #: 它们时拒绝（ADR-024 硬约束 2 对连接同样成立），不换模型、不回落。
    reasoning_models: frozenset[tuple[str, str]] = frozenset()

    @property
    def provider_id(self) -> str:
        return catalog.org_provider_id(self.connection_id)


def _models_of(row: OrgProviderConnection) -> tuple[tuple[str, str], ...]:
    out: list[tuple[str, str]] = []
    for item in row.models or []:
        if isinstance(item, dict) and item.get("model_id") and item.get("protocol"):
            out.append((str(item["model_id"]), str(item["protocol"])))
    return tuple(out)


def _reasoning_of(row: OrgProviderConnection) -> frozenset[tuple[str, str]]:
    """标了 `reasoning: true` 的模型。A3 之前存的条目没有这个键，按 false 处理，不需要改数据。"""
    return frozenset(
        (str(item["model_id"]), str(item["protocol"]))
        for item in row.models or []
        if isinstance(item, dict)
        and item.get("model_id")
        and item.get("protocol")
        and item.get("reasoning") is True
    )


async def load_connection(
    db: AsyncSession, *, org_id: uuid.UUID, connection_id: uuid.UUID
) -> ResolvedConnection | None:
    row = await repo.get_connection(db, org_id=org_id, connection_id=connection_id)
    if row is None:
        return None
    try:
        key = decrypt_secret(row.key_encrypted)
    except CryptoConfigError as exc:
        log.warning("upstreams.connection_decrypt_failed", org_id=str(org_id))
        raise AppError(
            "provider.byok.rejected",
            message=f"供应商「{row.label}」的 Key 无法解密：{exc}。请重新填写一次。",
            detail={"reason": "decrypt_failed", "connection_id": str(row.id)},
        ) from exc
    return ResolvedConnection(
        connection_id=row.id,
        label=row.label,
        preset_id=row.preset_id,
        base_url=row.base_url,
        models=_models_of(row),
        enabled=row.enabled,
        api_key=key,
        reasoning_models=_reasoning_of(row),
    )


def _rejected(capability: str, reason: str, message: str, **extra: str) -> AppError:
    return AppError(
        "provider.byok.rejected",
        message=message,
        detail={"capability": capability, "reason": reason, **extra},
    )


def connection_missing(capability: str, provider_id: str | None) -> AppError:
    return _rejected(
        capability,
        "connection_missing",
        f"{capability} 选的供应商连接已被删除或不存在，请到模型页重新选择",
        provider_id=str(provider_id),
    )


def pick_model(
    capability: str,
    *,
    label: str,
    models: tuple[tuple[str, str], ...],
    enabled: bool,
    model_id: str | None,
    connection_id: uuid.UUID,
) -> tuple[str, str]:
    """在一个连接里为这个能力选出 (模型, 协议)。任何对不上都报错，不换连接、不落平台。

    - 连接被停用 → `connection_disabled`；
    - 连接里没有这个能力的协议的模型 → `capability_mismatch`（不采信端点自我声明，
      能力只看代码白名单里协议绑定的那一个）；
    - 指定了模型却不在连接里 → `model_missing`；
    - 没指定 → 连接里这个能力的第一个模型。
    """
    cid = str(connection_id)
    if not enabled:
        raise _rejected(
            capability,
            "connection_disabled",
            f"供应商「{label}」已停用，请启用它或到模型页改选其他上游",
            connection_id=cid,
        )
    allowed = set(catalog.protocols_for(capability))
    candidates = [(m, p) for m, p in models if p in allowed]
    if not candidates:
        raise _rejected(
            capability,
            "capability_mismatch",
            f"供应商「{label}」里没有可用于 {capability} 的模型",
            connection_id=cid,
        )
    if model_id is None:
        return candidates[0]
    for model, protocol in candidates:
        if model == model_id:
            return model, protocol
    raise _rejected(
        capability,
        "model_missing",
        f"供应商「{label}」里已经没有模型 {model_id}，请到模型页重新选择",
        connection_id=cid,
    )


def check_reasoning(
    capability: str,
    connection: ResolvedConnection,
    *,
    model_id: str,
    protocol: str,
    allow_reasoning: bool,
) -> None:
    """`no_reasoning_roles` 的角色不许落到用户标成推理模型的连接模型上。

    平台目录的推理模型偏好在 `service._preferred_model` 里被丢掉、按默认顺序跑；连接不行——
    用户选的就是"这个供应商的这个模型"，丢掉偏好等于换模型，所以这里直接拒绝。
    """
    if allow_reasoning or (model_id, protocol) not in connection.reasoning_models:
        return
    raise _rejected(
        capability,
        "reasoning_model_not_allowed",
        f"供应商「{connection.label}」的模型 {model_id} 标记为推理模型，"
        "当前环节（分类 / 结构化抽取）不能用推理模型，请到模型页改选非推理模型",
        connection_id=str(connection.connection_id),
        model_id=model_id,
    )


def build_adapter(connection: ResolvedConnection, *, model_id: str, protocol: str) -> Any:
    """按协议白名单造适配器。协议 → 唯一适配器；库里的协议不在白名单就拒绝。"""
    spec = catalog.protocol_spec(protocol)
    if spec is None:
        raise AppError(
            "provider.byok.rejected",
            message=f"协议 {protocol!r} 不在白名单里",
            detail={"reason": "protocol_unsupported"},
        )
    kwargs: dict[str, Any] = {
        "base_url": connection.base_url,
        "api_key": connection.api_key,
        "model_id": model_id,
        "key_source": KeySource.ORG,
        "provider_id": connection.provider_id,
    }
    if protocol == "openai_images":
        preset = presets.get(connection.preset_id) if connection.preset_id else None
        kwargs["request_overrides"] = dict(preset.request_overrides) if preset else {}
    return spec.adapter(**kwargs)


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


async def describe(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    capability: str,
    project_preference: str | None = None,
) -> dict[str, Any]:
    """建任务时记进 `input_json.upstream` 的那一份（ADR-031 代价 2、ADR-039 代价 1）。

    与计费同一个 :func:`decide`。选中的是组织连接时**在这里就校验连接**——
    连接被删 / 停用 / 没有这个能力的模型，任务不建、钱不动，而不是预扣之后
    跑到 Worker 里才失败。
    """
    decision = decide(
        capability,
        preference=project_preference,
        default=await load_default(db, org_id=org_id, capability=capability),
    )
    model_id = decision.model_id
    if decision.is_org_connection:
        connection = (
            await load_connection(db, org_id=org_id, connection_id=decision.connection_id)
            if decision.connection_id
            else None
        )
        if connection is None:
            raise connection_missing(capability, decision.provider_id)
        model_id, _protocol = pick_model(
            capability,
            label=connection.label,
            models=connection.models,
            enabled=connection.enabled,
            model_id=decision.model_id,
            connection_id=connection.connection_id,
        )
    source = await effective_key_source(
        db, org_id=org_id, capability=capability, project_preference=project_preference
    )
    return {
        "capability": capability,
        "provider_id": decision.provider_id,
        "connection_id": str(decision.connection_id) if decision.connection_id else None,
        "model_id": model_id,
        "layer": decision.layer,
        "key_source": source.value,
    }


# ---------------------------------------------------------------- 配置视图


@dataclass(frozen=True, slots=True)
class ConnectionModelView:
    model_id: str
    protocol: str
    capability: str
    reasoning: bool = False


@dataclass(frozen=True, slots=True)
class ConnectionView:
    """组织连接的展示态。**没有 Key 明文**，只有尾号。"""

    id: uuid.UUID
    provider_id: str
    label: str
    preset_id: str | None
    base_url: str
    models: list[ConnectionModelView]
    enabled: bool
    masked_key: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SelectionView:
    provider_id: str | None
    model_id: str | None
    key_source: str
    #: `org` = 组织显式存过；`platform` = 没存过，走的是目录默认
    layer: Literal["org", "platform"]
    updated_at: datetime | None
    #: 组织默认指向的连接已删 / 停用 / 没有这个能力的模型时为原因码，界面据此提示。
    #: 不自动改默认：自动退回平台就是"静默落到平台"。
    broken_reason: str | None = None


def _connection_view(row: OrgProviderConnection) -> ConnectionView:
    try:
        masked: str | None = credentials.mask(decrypt_secret(row.key_encrypted))
    except CryptoConfigError:
        masked = None
    models: list[ConnectionModelView] = []
    reasoning = _reasoning_of(row)
    for model_id, protocol in _models_of(row):
        spec = catalog.protocol_spec(protocol)
        models.append(
            ConnectionModelView(
                model_id=model_id,
                protocol=protocol,
                capability=spec.capability if spec else "",
                reasoning=(model_id, protocol) in reasoning,
            )
        )
    return ConnectionView(
        id=row.id,
        provider_id=catalog.org_provider_id(row.id),
        label=row.label,
        preset_id=row.preset_id,
        base_url=row.base_url,
        models=models,
        enabled=row.enabled,
        masked_key=masked,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def list_connection_views(db: AsyncSession, *, org_id: uuid.UUID) -> list[ConnectionView]:
    return [_connection_view(row) for row in await repo.list_connections(db, org_id=org_id)]


async def connection_view(
    db: AsyncSession, *, org_id: uuid.UUID, connection_id: uuid.UUID
) -> ConnectionView:
    row = await _require_connection(db, org_id=org_id, connection_id=connection_id)
    return _connection_view(row)


async def _broken_reason(
    db: AsyncSession, *, org_id: uuid.UUID, capability: str, provider_id: str, model_id: str | None
) -> str | None:
    parsed = catalog.parse_org_ref(provider_id)
    if parsed is None:
        return None
    connection_id, _ = parsed
    row = (
        await repo.get_connection(db, org_id=org_id, connection_id=connection_id)
        if connection_id
        else None
    )
    if row is None:
        return "connection_missing"
    try:
        pick_model(
            capability,
            label=row.label,
            models=_models_of(row),
            enabled=row.enabled,
            model_id=model_id,
            connection_id=row.id,
        )
    except AppError as exc:
        return str((exc.detail or {}).get("reason") or exc.code)
    return None


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
            broken_reason=await _broken_reason(
                db,
                org_id=org_id,
                capability=capability,
                provider_id=row.provider_id,
                model_id=row.model_id,
            ),
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

    - 上游必须是这个能力下真有适配器的一家，或本组织的一个供应商连接；
    - 模型必须属于这家 / 这个连接——A 家的模型名配 B 家的 Key 是拒绝，不是"帮你换一家"；
    - 组织连接只能用自己的 Key 计费，且必须启用、有这个能力的模型；
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

    parsed = catalog.parse_org_ref(provider_id)
    if parsed is not None:
        connection_id, ref_model = parsed
        if connection_id is None or ref_model is not None:
            raise AppError(
                "provider.params.invalid",
                message="组织默认的上游写成 provider.org:<连接 id>，模型放在 model_id 里",
            )
        if not catalog.supports_org_connections(capability):
            raise AppError(
                "provider.params.invalid",
                message=f"{capability} 还不支持自带 Key 的供应商",
                detail={"capability": capability},
            )
        row = await _require_connection(db, org_id=org_id, connection_id=connection_id)
        if key_source != KeySource.ORG.value:
            raise AppError(
                "common.validation_failed",
                message="自带 Key 的供应商只能用你自己的 Key 计费",
            )
        try:
            pick_model(
                capability,
                label=row.label,
                models=_models_of(row),
                enabled=row.enabled,
                model_id=model_id,
                connection_id=row.id,
            )
        except AppError as exc:
            # 存配置时的不合法组合是 4xx，不是"上游调用失败"
            raise AppError(
                "provider.params.invalid", message=exc.message, detail=exc.detail
            ) from exc
        provider_id = catalog.org_provider_id(row.id)
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


async def validate_preference_ref(
    db: AsyncSession, *, org_id: uuid.UUID, capability: str, value: str
) -> None:
    """项目偏好要存 `provider.org:<id>[:<模型>]` 之前的校验（项目模块调用）。

    连接不在本 org → 404（不确认别人的连接存在）；能力对不上 / 模型不在连接里 /
    连接停用 → 400。存进去之后连接再变，解析时报可读错误，不静默改偏好。
    """
    parsed = catalog.parse_org_ref(value)
    if parsed is None or parsed[0] is None:
        raise AppError(
            "provider.params.invalid",
            message="供应商引用的格式是 provider.org:<连接 id> 或 provider.org:<连接 id>:<模型 id>",
        )
    connection_id, model_id = parsed
    assert connection_id is not None
    if not catalog.supports_org_connections(capability):
        raise AppError(
            "provider.params.invalid",
            message=f"{capability} 还不支持自带 Key 的供应商",
            detail={"capability": capability},
        )
    row = await _require_connection(db, org_id=org_id, connection_id=connection_id)
    try:
        pick_model(
            capability,
            label=row.label,
            models=_models_of(row),
            enabled=row.enabled,
            model_id=model_id,
            connection_id=row.id,
        )
    except AppError as exc:
        raise AppError("provider.params.invalid", message=exc.message, detail=exc.detail) from exc


# ---------------------------------------------------------------- 写：供应商连接


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
    try:
        return presets.check_model_id(model_id)
    except ValueError as exc:
        raise AppError(
            "common.validation_failed",
            message=f"模型 ID 最长 {MAX_MODEL_ID_CHARS} 个字符，且不能含空白",
            detail={"field": "model_id"},
        ) from exc


def clean_models(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """规整模型列表：协议必须在白名单里，模型 id 合法，(模型, 协议) 去重，至少一个。

    每条存成 `{model_id, protocol, reasoning}`；`reasoning` 缺省为 false。
    """
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in models:
        model_id, protocol = str(item.get("model_id") or ""), str(item.get("protocol") or "")
        reasoning = item.get("reasoning", False)
        if not isinstance(reasoning, bool):
            raise AppError(
                "common.validation_failed",
                message="reasoning 只能是 true / false",
                detail={"field": "models"},
            )
        if catalog.protocol_spec(protocol) is None:
            raise AppError(
                "common.validation_failed",
                message=f"协议 {protocol!r} 不受支持",
                detail={"field": "models", "allowed": sorted(catalog.PROTOCOLS)},
            )
        clean = _clean_model_id(model_id)
        if (clean, protocol) in seen:
            continue
        seen.add((clean, protocol))
        out.append({"model_id": clean, "protocol": protocol, "reasoning": reasoning})
    if not out:
        raise AppError(
            "common.validation_failed",
            message="至少填写一个模型",
            detail={"field": "models"},
        )
    if len(out) > MAX_MODELS_PER_CONNECTION:
        raise AppError(
            "common.validation_failed",
            message=f"一个供应商最多 {MAX_MODELS_PER_CONNECTION} 个模型",
            detail={"field": "models"},
        )
    return out


async def _require_connection(
    db: AsyncSession, *, org_id: uuid.UUID, connection_id: uuid.UUID
) -> OrgProviderConnection:
    row = await repo.get_connection(db, org_id=org_id, connection_id=connection_id)
    if row is None:
        # 别的 org 的连接与不存在的连接一样是 404，不确认它存在
        raise AppError("common.not_found", message="供应商连接不存在")
    return row


async def create_connection(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    preset_id: str | None,
    label: str | None,
    base_url: str | None,
    models: list[dict[str, Any]] | None,
    api_key: str,
    enabled: bool = True,
) -> ConnectionView:
    """新建连接。从预设建时没给的字段从预设拷（ADR-039 第 3 条："选预设=把模板拷进一行"）。

    Base URL 不论来自预设还是用户，都过同一道 `normalize_base_url`。
    """
    preset = None
    if preset_id is not None:
        preset = presets.get(preset_id)
        if preset is None:
            raise AppError(
                "common.validation_failed",
                message=f"没有预设 {preset_id!r}",
                detail={"field": "preset_id"},
            )
    limit = get_settings().org_provider_connection_limit
    if await repo.count_connections(db, org_id=org_id) >= limit:
        raise AppError(
            "common.validation_failed",
            message=f"一个组织最多添加 {limit} 个供应商",
            detail={"limit": limit},
        )
    clean_label = _clean_label(label if label is not None else (preset.label if preset else ""))
    raw_url = base_url if base_url is not None else (preset.base_url if preset else "")
    clean_url = endpoint_url.normalize_base_url(raw_url)
    raw_models: list[dict[str, Any]] = (
        models
        if models is not None
        else [
            {"model_id": m.model_id, "protocol": m.protocol, "reasoning": m.reasoning}
            for m in preset.models
        ]
        if preset
        else []
    )
    clean = clean_models(raw_models)
    if all(m["protocol"] in catalog.OPENAI_BASE_PROTOCOLS for m in clean):
        clean_url = normalize_api_base(clean_url)
    key = credentials.clean_key(api_key)
    row = await repo.create_connection(
        db,
        org_id=org_id,
        label=clean_label,
        preset_id=preset_id,
        base_url=clean_url,
        key_encrypted=encrypt_secret(key),
        models=clean,
        enabled=enabled,
        created_by=user_id,
    )
    view = _connection_view(row)
    await db.commit()
    # 日志里没有 Key、没有地址 path（path 里偶尔有租户号 / 工作区号）
    log.info(
        "upstreams.connection_created",
        org_id=str(org_id),
        connection_id=str(view.id),
        preset_id=preset_id,
        models=len(clean),
    )
    return view


async def update_connection(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    label: str | None = None,
    base_url: str | None = None,
    models: list[dict[str, Any]] | None = None,
    enabled: bool | None = None,
    api_key: str | None = None,
) -> ConnectionView:
    """改连接。没给的字段不动；`api_key` 不给就沿用原来那把（前端拿不回明文）。"""
    row = await _require_connection(db, org_id=org_id, connection_id=connection_id)
    changes: dict[str, Any] = {}
    if label is not None:
        changes["label"] = _clean_label(label)
    if base_url is not None:
        changes["base_url"] = endpoint_url.normalize_base_url(base_url)
    if models is not None:
        changes["models"] = clean_models(models)
    effective_models = changes.get("models", row.models)
    if base_url is not None and all(
        m["protocol"] in catalog.OPENAI_BASE_PROTOCOLS for m in effective_models
    ):
        changes["base_url"] = normalize_api_base(base_url)
    if enabled is not None:
        changes["enabled"] = bool(enabled)
    if api_key is not None:
        changes["key_encrypted"] = encrypt_secret(credentials.clean_key(api_key))
    if changes:
        row = await repo.update_connection(db, row=row, changes=changes)
    view = _connection_view(row)
    await db.commit()
    log.info(
        "upstreams.connection_updated",
        org_id=str(org_id),
        connection_id=str(connection_id),
        fields=sorted(k for k in changes if k != "key_encrypted")
        + (["api_key"] if "key_encrypted" in changes else []),
    )
    return view


async def delete_connection(
    db: AsyncSession, *, org_id: uuid.UUID, connection_id: uuid.UUID
) -> None:
    """软删连接。

    **指向它的组织默认 / 项目偏好不自动改**：多连接下自动退回平台默认，等于在用户
    不知情时把计费从他的 Key 换到平台额度。保留引用，解析时报可读错误
    （`connection_missing`），模型页的 `selection.broken_reason` 也会标出来。
    """
    row = await _require_connection(db, org_id=org_id, connection_id=connection_id)
    await repo.soft_delete_connection(db, row=row)
    await db.commit()
    log.info("upstreams.connection_removed", org_id=str(org_id), connection_id=str(connection_id))


@dataclass(frozen=True, slots=True)
class ConnectionReferences:
    """谁正指着这个连接。删除确认框用：删除不自动清默认 / 偏好（ADR-039 决定 (d)）。"""

    #: 组织默认里指向它的能力
    default_capabilities: list[str]
    #: (项目 id, 项目标题, 能力)；只算未软删的项目
    projects: list[tuple[uuid.UUID, str, str]]


async def connection_references(
    db: AsyncSession, *, org_id: uuid.UUID, connection_id: uuid.UUID
) -> ConnectionReferences:
    """只读。连接不在本 org（或不存在 / 已删）→ 404，不确认别人的连接存在。"""
    row = await _require_connection(db, org_id=org_id, connection_id=connection_id)
    ref = catalog.org_provider_id(row.id)
    defaults = sorted(
        d.capability for d in await repo.list_defaults(db, org_id=org_id) if d.provider_id == ref
    )
    # 延迟导入：项目模块读偏好时会回头用到本模块（`validate_preference_ref`）
    from apps.api.modules.project import service as project_service

    projects = await project_service.list_preference_references(db, org_id=org_id, provider_ref=ref)
    return ConnectionReferences(default_capabilities=defaults, projects=projects)


async def discover_connection_models(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    base_url: str,
    api_key: str | None,
    connection_id: uuid.UUID | None = None,
) -> ConnectionDiscoverOut:
    from adapters.providers.model_discovery import discover_models, normalize_api_base

    saved = (
        await _require_connection(db, org_id=org_id, connection_id=connection_id)
        if connection_id is not None
        else None
    )
    key = (
        credentials.clean_key(api_key)
        if api_key is not None
        else decrypt_secret(saved.key_encrypted)
        if saved is not None
        else None
    )
    if not key:
        raise AppError("common.validation_failed", message="请填写 API Key")
    base = normalize_api_base(base_url)
    if get_settings().env == "test":
        return ConnectionDiscoverOut(base_url=base, models=["mock.text.v1"], mock=True)
    models = await discover_models(base_url=base, api_key=key)
    return ConnectionDiscoverOut(base_url=base, models=models)


async def test_connection(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    connection_id: uuid.UUID | None = None,
    protocol: str | None = None,
    model_id: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    preset_id: str | None = None,
) -> probe.ProbeResult:
    """测试连接：按协议发一次**不花钱**的最小请求（`verify_key`）。

    给了 `connection_id` 就用已保存的值兜底没给的字段——Key 明文前端拿不回来，
    只能让后端自己解。与正式调用是**同一个适配器类**、同样的地址规整与出网校验。
    地址不合法照样返回"测试结论"而不是 4xx，与 `probe.verify` 的约定一致。
    `ENV=test` 时走 Mock 探测，不打用户填的地址。
    """
    saved = (
        await _require_connection(db, org_id=org_id, connection_id=connection_id)
        if connection_id is not None
        else None
    )
    saved_models = _models_of(saved) if saved is not None else ()
    proto = protocol or (saved_models[0][1] if saved_models else None)
    if proto is not None and catalog.protocol_spec(proto) is None:
        raise AppError(
            "common.validation_failed",
            message=f"协议 {proto!r} 不受支持",
            detail={"field": "protocol", "allowed": sorted(catalog.PROTOCOLS)},
        )
    model = model_id or next((m for m, p in saved_models if p == proto), None)
    url = base_url if base_url is not None else (saved.base_url if saved else None)
    if api_key is not None:
        key: str | None = credentials.clean_key(api_key)
    elif saved is not None:
        try:
            key = decrypt_secret(saved.key_encrypted)
        except CryptoConfigError:
            key = None
    else:
        key = None
    if not url or not model or not key or not proto:
        raise AppError(
            "common.validation_failed",
            message="测试连接需要地址、协议、模型与 Key",
        )

    pid = catalog.org_provider_id(saved.id) if saved is not None else catalog.ORG_PROVIDER_PREFIX
    connection = ResolvedConnection(
        connection_id=saved.id if saved is not None else uuid.UUID(int=0),
        label=saved.label if saved is not None else "",
        preset_id=preset_id or (saved.preset_id if saved is not None else None),
        base_url=url,
        models=((model, proto),),
        enabled=True,
        api_key=key,
    )
    try:
        if proto in catalog.OPENAI_BASE_PROTOCOLS:
            # 草稿地址与保存时同一道规整：粘完整请求地址测试，不能测成 `.../chat/completions/models`
            connection = replace(connection, base_url=normalize_api_base(url))
        adapter = build_adapter(connection, model_id=_clean_model_id(model), protocol=proto)
    except AppError as exc:
        return probe.ProbeResult(
            ok=False, provider_id=pid, message=exc.message, error_code=exc.code
        )
    return await probe.verify_with(adapter, provider_id=pid, api_key=key)
