"""模型目录：只读地告诉前端"有哪些能力、每个能力能选哪几个模型"。

只读，也只有一条 GET。真正的选择动作落在项目上
（`PATCH /projects/{id}/model-preference`，ADR-024 说覆盖发生在项目层），
这里不承担任何写入。

**没有平台 Key 的 Provider 照样出现在目录里**，`available` 仍是 true。
它描述的是"平台接没接这家"，不是"这台机器此刻配没配 Key"——后者是部署状态，
用它去关掉界面上的选项，会让本地无 Key 的开发环境看起来像功能没做完。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from fastapi import APIRouter, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.config import get_settings
from apps.api.modules.auth.deps import CurrentUser, DbSession
from apps.api.modules.billing import credentials
from apps.api.modules.billing.schemas import ProviderKeyTestOut
from apps.api.modules.gateway import catalog, presets, upstreams
from apps.api.modules.gateway.probe import ProbeResult
from apps.api.modules.gateway.schemas import (
    CapabilityConfigOut,
    CapabilityModelsOut,
    ConnectionCreateIn,
    ConnectionDefaultRefOut,
    ConnectionDiscoverIn,
    ConnectionDiscoverOut,
    ConnectionDraftTestIn,
    ConnectionListOut,
    ConnectionModelOut,
    ConnectionOut,
    ConnectionProjectRefOut,
    ConnectionReferencesOut,
    ConnectionTestIn,
    ConnectionUpdateIn,
    CredentialStatusOut,
    ModelCatalogOut,
    ModelConfigOut,
    ModelOptionOut,
    PresetListOut,
    PresetModelOut,
    PresetOut,
    ProtocolOut,
    ProviderOptionOut,
    SelectionIn,
    SelectionOut,
)
from apps.api.modules.gateway.upstreams import ConnectionView, SelectionView
from skills import registry as skill_registry

router = APIRouter(prefix="/model-catalog", tags=["model-catalog"])

# 声明了但平台还没接的能力为什么没接。写成一句话由后端给，
# 是为了让前端不必自己判断"哪些算视频、视频在哪个里程碑"——
# 那两件事都会变，且前端猜错了就是在界面上说谎。
M2_PENDING = "M2 待办：语音与视频链路（TTS → 音频优先时间线 → ffmpeg 合成）尚未接入"

# 展示顺序。先文字后图像再未接入的，跟生产链路本身的顺序一致。
_ORDER = ("text_generation", "image_generation", "text_to_speech", "image_to_video")


def _declared_capabilities() -> list[str]:
    """目录里该出现哪些能力。

    两个来源取并集：Skill 声明用户可改的（`model_policy.user_selectable`）
    和平台真的接了适配器的。只取交集的话，"声明了但还没接"的能力
    （视频、语音）就会从界面上凭空消失——用户看不到它，只会以为
    这个产品压根没打算做，而不是"在做，排在 M2"。
    """
    declared: set[str] = set(catalog.provider_of())
    for spec in skill_registry.registry().specs.values():
        declared.update(spec.model_policy.user_selectable)
    ranked = [c for c in _ORDER if c in declared]
    return ranked + sorted(declared - set(ranked))


def _selectable() -> set[str]:
    out: set[str] = set()
    for spec in skill_registry.registry().specs.values():
        out.update(spec.model_policy.user_selectable)
    return out


def _model_options(spec: catalog.ProviderSpec) -> list[ModelOptionOut]:
    return [
        ModelOptionOut(
            model_id=model_id,
            # 目录里出现了却没写展示信息的模型，退回裸 id 而不是隐藏它：
            # 它是真的可选，藏起来才是错的。
            label=catalog.MODEL_LABELS.get(model_id, (model_id, ""))[0],
            note=catalog.MODEL_LABELS.get(model_id, (model_id, ""))[1],
            reasoning=catalog.is_reasoning(model_id),
        )
        for model_id, _priority in spec.models
    ]


def _consistency_verified(protocols: Iterable[str]) -> bool | None:
    """一个选项下几个协议合起来的"实测过没有"：有一个没测过就是没测过；全是文本协议为 None。"""
    flags = [catalog.consistency_verified(p) for p in protocols]
    known = [f for f in flags if f is not None]
    return all(known) if known else None


def _provider_options(
    capability: str, connections: list[ConnectionView] | None = None
) -> list[ProviderOptionOut]:
    """这个能力下可选的上游。**来自目录与组织连接，不在前端复制一份。**

    组织连接（ADR-039）按协议推出能力：连接里有这个能力的协议的模型，它就出现在
    这个能力下，模型只列那几个。停用的连接照样列出但 `available=False`——
    默认可能正指着它，界面要能说清"为什么不能用"。
    """
    out = [
        ProviderOptionOut(
            provider_id=spec.provider_id,
            label=spec.label,
            kind="catalog",
            available=True,
            models=_model_options(spec),
            default_model_id=spec.models[0][0] if spec.models else None,
            supports_platform_key=True,
        )
        for spec in catalog.providers_for(capability)
    ]
    for conn in connections or []:
        models = [m for m in conn.models if m.capability == capability]
        if not models:
            continue
        out.append(
            ProviderOptionOut(
                provider_id=conn.provider_id,
                label=conn.label,
                kind="org",
                available=conn.enabled,
                models=[
                    ModelOptionOut(
                        model_id=m.model_id,
                        label=m.model_id,
                        note=conn.base_url,
                        reasoning=m.reasoning,
                    )
                    for m in models
                ],
                default_model_id=models[0].model_id,
                supports_platform_key=False,
                unavailable_reason=None if conn.enabled else "该供应商已停用",
                connection_id=conn.id,
                consistency_verified=_consistency_verified(m.protocol for m in models),
            )
        )
    return out


def _item(capability: str, *, selectable: set[str]) -> CapabilityModelsOut:
    spec = catalog.spec_for_capability(capability)
    label = credentials.CAPABILITY_LABELS.get(capability, capability)

    if spec is None:
        return CapabilityModelsOut(
            capability=capability,
            label=label,
            available=False,
            provider_id=None,
            provider_label=None,
            user_selectable=capability in selectable,
            models=[],
            default_model_id=None,
            unavailable_reason=M2_PENDING,
        )

    models = _model_options(spec)
    return CapabilityModelsOut(
        capability=capability,
        label=label,
        available=True,
        provider_id=spec.provider_id,
        provider_label=spec.label,
        user_selectable=capability in selectable,
        models=models,
        # `spec.models` 已按优先级排好，第一个就是 Gateway 不带偏好时先试的那个
        default_model_id=models[0].model_id if models else None,
        unavailable_reason=None,
    )


@router.get("", response_model=ModelCatalogOut)
async def list_model_catalog(user: CurrentUser) -> ModelCatalogOut:
    """列出所有能力及其可选模型。

    目录本身对所有租户是同一份（它描述的是平台接了什么），所以不查库。
    仍然要求登录：未登录的访客没有理由知道平台正在用哪几家上游。
    """
    del user
    selectable = _selectable()
    return ModelCatalogOut(
        items=[_item(c, selectable=selectable) for c in _declared_capabilities()]
    )


# ---------------------------------------------------------------- 组织级上游配置

config_router = APIRouter(prefix="/model-config", tags=["model-config"])


def _selection_out(view: SelectionView) -> SelectionOut:
    return SelectionOut(
        provider_id=view.provider_id,
        model_id=view.model_id,
        key_source="org" if view.key_source == "org" else "platform",
        layer=view.layer,
        updated_at=view.updated_at,
        broken_reason=view.broken_reason,
    )


def _connection_out(view: ConnectionView) -> ConnectionOut:
    return ConnectionOut(
        id=view.id,
        provider_id=view.provider_id,
        label=view.label,
        preset_id=view.preset_id,
        base_url=view.base_url,
        models=[
            ConnectionModelOut(
                model_id=m.model_id,
                protocol=m.protocol,
                capability=m.capability,
                consistency_verified=catalog.consistency_verified(m.protocol),
                reasoning=m.reasoning,
            )
            for m in view.models
        ],
        enabled=view.enabled,
        masked_key=view.masked_key,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


def _probe_out(result: ProbeResult) -> ProviderKeyTestOut:
    return ProviderKeyTestOut(
        ok=result.ok,
        provider_id=result.provider_id,
        message=result.message,
        error_code=result.error_code,
    )


async def _config(db: AsyncSession, org_id: uuid.UUID) -> ModelConfigOut:
    configurable = set(credentials.configurable_capabilities())
    keys = await credentials.list_for_org(db, org_id=org_id)
    connections = await upstreams.list_connection_views(db, org_id=org_id)
    items: list[CapabilityConfigOut] = []
    for capability in _declared_capabilities():
        label = credentials.CAPABILITY_LABELS.get(capability, capability)
        if not catalog.providers_for(capability):
            items.append(
                CapabilityConfigOut(
                    capability=capability,
                    label=label,
                    available=False,
                    configurable=False,
                    providers=[],
                    selection=None,
                    credentials=[],
                    unavailable_reason=M2_PENDING,
                )
            )
            continue
        items.append(
            CapabilityConfigOut(
                capability=capability,
                label=label,
                available=True,
                configurable=capability in configurable,
                providers=_provider_options(capability, connections),
                selection=_selection_out(
                    await upstreams.selection_view(db, org_id=org_id, capability=capability)
                ),
                credentials=[
                    CredentialStatusOut(
                        provider_id=k.provider_id,
                        configured=k.configured,
                        masked_key=k.masked_key,
                        updated_at=k.updated_at,
                    )
                    for k in keys
                    if k.capability == capability
                ],
                supports_org_connections=catalog.supports_org_connections(capability),
            )
        )
    return ModelConfigOut(items=items)


@config_router.get("", response_model=ModelConfigOut)
async def get_model_config(user: CurrentUser, db: DbSession) -> ModelConfigOut:
    """每个能力：可选上游与模型（目录 + 组织连接）、组织当前选择、各家 Key 状态。

    一次给全，模型页不需要自己拼三个接口——拼出来的那份迟早和解析层对不上。
    """
    return await _config(db, user.org_id)


# 预设与连接的路由必须注册在 `/{capability}` 之前，免得被它吃掉。


@config_router.get("/presets", response_model=PresetListOut)
async def list_presets(user: CurrentUser) -> PresetListOut:
    """供应商预设（只含事实，不含价格）+ 协议白名单。对所有租户同一份。"""
    del user
    out: list[PresetOut] = []
    for preset in presets.presets():
        capabilities = [
            spec.capability
            for p in preset.protocols
            if (spec := catalog.protocol_spec(p)) is not None
        ]
        out.append(
            PresetOut(
                preset_id=preset.preset_id,
                label=preset.label,
                base_url=preset.base_url,
                docs_url=preset.docs_url,
                key_url=preset.key_url,
                icon=preset.icon,
                protocols=list(preset.protocols),
                capabilities=list(dict.fromkeys(capabilities)),
                models=[
                    PresetModelOut(
                        model_id=m.model_id,
                        protocol=m.protocol,
                        capability=catalog.PROTOCOLS[m.protocol].capability,
                        consistency_verified=catalog.consistency_verified(m.protocol),
                        reasoning=m.reasoning,
                    )
                    for m in preset.models
                ],
            )
        )
    return PresetListOut(
        presets=out,
        protocols=[
            ProtocolOut(
                protocol=p.protocol,
                capability=p.capability,
                label=p.label,
                consistency_verified=p.consistency_verified,
            )
            for p in catalog.PROTOCOLS.values()
        ],
    )


@config_router.get("/connections", response_model=ConnectionListOut)
async def list_connections(user: CurrentUser, db: DbSession) -> ConnectionListOut:
    views = await upstreams.list_connection_views(db, org_id=user.org_id)
    return ConnectionListOut(
        items=[_connection_out(v) for v in views],
        limit=get_settings().org_provider_connection_limit,
    )


@config_router.post(
    "/connections", response_model=ConnectionOut, status_code=status.HTTP_201_CREATED
)
async def create_connection(
    payload: ConnectionCreateIn, user: CurrentUser, db: DbSession
) -> ConnectionOut:
    """新建连接（从预设或自定义）。Key 只加密入库，响应里只有尾号。"""
    view = await upstreams.create_connection(
        db,
        org_id=user.org_id,
        user_id=user.id,
        preset_id=payload.preset_id,
        label=payload.label,
        base_url=payload.base_url,
        models=[m.model_dump() for m in payload.models] if payload.models is not None else None,
        api_key=payload.api_key,
        enabled=payload.enabled,
    )
    return _connection_out(view)


@config_router.post("/connections/test", response_model=ProviderKeyTestOut)
async def test_draft_connection(
    payload: ConnectionDraftTestIn, user: CurrentUser, db: DbSession
) -> ProviderKeyTestOut:
    """保存之前测试连接。按协议发一次不花钱的最小请求；结论在 `ok` 里，失败原因已脱敏。"""
    result = await upstreams.test_connection(
        db,
        org_id=user.org_id,
        protocol=payload.protocol,
        model_id=payload.model_id,
        base_url=payload.base_url,
        api_key=payload.api_key,
        preset_id=payload.preset_id,
    )
    return _probe_out(result)


@config_router.post("/connections/discover", response_model=ConnectionDiscoverOut)
async def discover_draft_models(
    payload: ConnectionDiscoverIn, user: CurrentUser, db: DbSession
) -> ConnectionDiscoverOut:
    return await upstreams.discover_connection_models(
        db, org_id=user.org_id, base_url=payload.base_url, api_key=payload.api_key
    )


@config_router.post("/connections/{connection_id}/discover", response_model=ConnectionDiscoverOut)
async def discover_saved_models(
    connection_id: uuid.UUID, payload: ConnectionDiscoverIn, user: CurrentUser, db: DbSession
) -> ConnectionDiscoverOut:
    return await upstreams.discover_connection_models(
        db,
        org_id=user.org_id,
        connection_id=connection_id,
        base_url=payload.base_url,
        api_key=payload.api_key,
    )


@config_router.get("/connections/{connection_id}", response_model=ConnectionOut)
async def get_connection(
    connection_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> ConnectionOut:
    """单个连接的展示态。别的组织的连接与不存在的一样 404。"""
    return _connection_out(
        await upstreams.connection_view(db, org_id=user.org_id, connection_id=connection_id)
    )


@config_router.get(
    "/connections/{connection_id}/references", response_model=ConnectionReferencesOut
)
async def get_connection_references(
    connection_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> ConnectionReferencesOut:
    """谁正指着这个连接：组织默认的能力、项目偏好（只算未删项目）。删除确认框用。"""
    refs = await upstreams.connection_references(
        db, org_id=user.org_id, connection_id=connection_id
    )
    return ConnectionReferencesOut(
        defaults=[ConnectionDefaultRefOut(capability=c) for c in refs.default_capabilities],
        projects=[
            ConnectionProjectRefOut(project_id=pid, name=name, capability=capability)
            for pid, name, capability in refs.projects
        ],
    )


@config_router.patch("/connections/{connection_id}", response_model=ConnectionOut)
async def update_connection(
    connection_id: uuid.UUID, payload: ConnectionUpdateIn, user: CurrentUser, db: DbSession
) -> ConnectionOut:
    view = await upstreams.update_connection(
        db,
        org_id=user.org_id,
        connection_id=connection_id,
        label=payload.label,
        base_url=payload.base_url,
        models=[m.model_dump() for m in payload.models] if payload.models is not None else None,
        enabled=payload.enabled,
        api_key=payload.api_key,
    )
    return _connection_out(view)


@config_router.delete("/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(connection_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    await upstreams.delete_connection(db, org_id=user.org_id, connection_id=connection_id)


@config_router.post("/connections/{connection_id}/test", response_model=ProviderKeyTestOut)
async def test_saved_connection(
    connection_id: uuid.UUID, payload: ConnectionTestIn, user: CurrentUser, db: DbSession
) -> ProviderKeyTestOut:
    """测试已保存的连接。与正式调用同一个适配器；`ENV=test` 走 Mock 探测。"""
    result = await upstreams.test_connection(
        db,
        org_id=user.org_id,
        connection_id=connection_id,
        protocol=payload.protocol,
        model_id=payload.model_id,
        base_url=payload.base_url,
        api_key=payload.api_key,
    )
    return _probe_out(result)


@config_router.put("/{capability}", response_model=ModelConfigOut)
async def put_selection(
    capability: str, payload: SelectionIn, user: CurrentUser, db: DbSession
) -> ModelConfigOut:
    """保存组织默认：上游 + 模型 + 计费来源。不合法的组合 4xx，不存。"""
    await upstreams.set_default(
        db,
        org_id=user.org_id,
        user_id=user.id,
        capability=capability,
        provider_id=payload.provider_id,
        model_id=payload.model_id,
        key_source=payload.key_source,
    )
    return await _config(db, user.org_id)
