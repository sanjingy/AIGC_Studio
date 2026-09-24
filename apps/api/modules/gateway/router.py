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

from fastapi import APIRouter, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.modules.auth.deps import CurrentUser, DbSession
from apps.api.modules.billing import credentials
from apps.api.modules.billing.schemas import ProviderKeyTestOut
from apps.api.modules.gateway import catalog, upstreams
from apps.api.modules.gateway.schemas import (
    CapabilityConfigOut,
    CapabilityModelsOut,
    CredentialStatusOut,
    EndpointIn,
    EndpointOut,
    EndpointTestIn,
    ModelCatalogOut,
    ModelConfigOut,
    ModelOptionOut,
    ProviderOptionOut,
    SelectionIn,
    SelectionOut,
)
from apps.api.modules.gateway.upstreams import EndpointView, SelectionView
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
        )
        for model_id, _priority in spec.models
    ]


def _provider_options(
    capability: str, endpoint: EndpointView | None = None
) -> list[ProviderOptionOut]:
    """这个能力下可选的上游。**来自目录，不在前端复制一份。**

    文本能力额外带一项自定义端点：配了就是可选的一家（模型就是端点上填的那个），
    没配就 `available=False` 并说明原因——界面据此提示"先填写端点"，
    不画一个选了会在第一次生成时才报错的选项。
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
    if catalog.supports_custom_endpoint(capability):
        out.append(
            ProviderOptionOut(
                provider_id=catalog.CUSTOM_TEXT_PROVIDER_ID,
                label=endpoint.label if endpoint else catalog.CUSTOM_TEXT_LABEL,
                kind="custom",
                available=endpoint is not None,
                models=(
                    [
                        ModelOptionOut(
                            model_id=endpoint.model_id, label="自定义", note=endpoint.base_url
                        )
                    ]
                    if endpoint
                    else []
                ),
                default_model_id=endpoint.model_id if endpoint else None,
                supports_platform_key=False,
                unavailable_reason=None if endpoint else "还没有填写自定义端点的地址、模型与 Key",
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
    )


def _endpoint_out(view: EndpointView | None) -> EndpointOut | None:
    if view is None:
        return None
    return EndpointOut(
        label=view.label,
        base_url=view.base_url,
        model_id=view.model_id,
        masked_key=view.masked_key,
        updated_at=view.updated_at,
    )


async def _config(db: AsyncSession, org_id: uuid.UUID) -> ModelConfigOut:
    configurable = set(credentials.configurable_capabilities())
    keys = await credentials.list_for_org(db, org_id=org_id)
    endpoint = await upstreams.endpoint_view(db, org_id=org_id)
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
        text_endpoint = endpoint if catalog.supports_custom_endpoint(capability) else None
        items.append(
            CapabilityConfigOut(
                capability=capability,
                label=label,
                available=True,
                configurable=capability in configurable,
                providers=_provider_options(capability, text_endpoint),
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
                custom_endpoint=_endpoint_out(text_endpoint),
                supports_custom_endpoint=catalog.supports_custom_endpoint(capability),
            )
        )
    return ModelConfigOut(items=items)


@config_router.get("", response_model=ModelConfigOut)
async def get_model_config(user: CurrentUser, db: DbSession) -> ModelConfigOut:
    """每个能力：可选上游与模型（目录）、组织当前选择、各家 Key 状态、自定义端点。

    一次给全，模型页不需要自己拼三个接口——拼出来的那份迟早和解析层对不上。
    """
    return await _config(db, user.org_id)


# 自定义端点的三条路由必须注册在 `/{capability}` 之前：否则
# `PUT /text_generation/custom-endpoint` 会先被 `/{capability}` 的 PUT 吃掉。


@config_router.put("/text_generation/custom-endpoint", response_model=ModelConfigOut)
async def put_custom_endpoint(
    payload: EndpointIn, user: CurrentUser, db: DbSession
) -> ModelConfigOut:
    """保存文本自定义端点。**路径里写死 text_generation**：没有别的能力能走到这里。"""
    await upstreams.save_endpoint(
        db,
        org_id=user.org_id,
        user_id=user.id,
        label=payload.label,
        base_url=payload.base_url,
        model_id=payload.model_id,
        api_key=payload.api_key,
    )
    return await _config(db, user.org_id)


@config_router.delete("/text_generation/custom-endpoint", status_code=status.HTTP_204_NO_CONTENT)
async def delete_custom_endpoint(user: CurrentUser, db: DbSession) -> None:
    await upstreams.delete_endpoint(db, org_id=user.org_id)


@config_router.post("/text_generation/custom-endpoint/test", response_model=ProviderKeyTestOut)
async def test_custom_endpoint(
    payload: EndpointTestIn, user: CurrentUser, db: DbSession
) -> ProviderKeyTestOut:
    """测试连接。与正式调用同一个适配器；结论在 `ok` 里，失败原因已脱敏。"""
    result = await upstreams.test_endpoint(
        db,
        org_id=user.org_id,
        base_url=payload.base_url,
        model_id=payload.model_id,
        api_key=payload.api_key,
    )
    return ProviderKeyTestOut(
        ok=result.ok,
        provider_id=result.provider_id,
        message=result.message,
        error_code=result.error_code,
    )


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
