"""模型目录：只读地告诉前端"有哪些能力、每个能力能选哪几个模型"。

只读，也只有一条 GET。真正的选择动作落在项目上
（`PATCH /projects/{id}/model-preference`，ADR-024 说覆盖发生在项目层），
这里不承担任何写入。

**没有平台 Key 的 Provider 照样出现在目录里**，`available` 仍是 true。
它描述的是"平台接没接这家"，不是"这台机器此刻配没配 Key"——后者是部署状态，
用它去关掉界面上的选项，会让本地无 Key 的开发环境看起来像功能没做完。
"""

from __future__ import annotations

from fastapi import APIRouter

from apps.api.modules.auth.deps import CurrentUser
from apps.api.modules.billing import credentials
from apps.api.modules.gateway import catalog
from apps.api.modules.gateway.schemas import (
    CapabilityModelsOut,
    ModelCatalogOut,
    ModelOptionOut,
)
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

    models = [
        ModelOptionOut(
            model_id=model_id,
            # 目录里出现了却没写展示信息的模型，退回裸 id 而不是隐藏它：
            # 它是真的可选，藏起来才是错的。
            label=catalog.MODEL_LABELS.get(model_id, (model_id, ""))[0],
            note=catalog.MODEL_LABELS.get(model_id, (model_id, ""))[1],
        )
        for model_id, _priority in spec.models
    ]
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
