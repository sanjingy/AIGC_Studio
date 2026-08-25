"""用户自带 Key（BYOK）的服务层（ADR-025）。

放在 billing 而不是 gateway，理由同 `models.py` 里那张表：**它存在的
理由是计费**——配了自己的 Key，该能力的估价就跳过 `model_pricing` 的
provider_cost 加价，只收 `pricing_rules` 里的隐性成本档位。

**明文的生命周期**：只在三个瞬间存在于内存——入库前加密、探测时发给上游、
读列表时算尾号。除此之外任何地方都拿不到它：不返回前端、不进日志、
不进 `input_json`。这不是本模块新加的约束，是 CLAUDE.md 那条的直接推论。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.crypto import CryptoConfigError, decrypt_secret, encrypt_secret
from apps.api.core.errors import AppError
from apps.api.core.logging import get_logger
from apps.api.modules.billing import repository as repo
from apps.api.modules.billing.models import ProviderCredential
from apps.api.modules.gateway import probe
from skills import registry as skill_registry

log = get_logger(__name__)

# 展示用的能力名。前端不该把 `text_generation` 这种枚举直接怼给用户，
# 而能配哪些能力是后端定的（见 configurable_capabilities），
# 所以文案也由后端给——否则前端遇到没见过的能力只能显示原始枚举。
CAPABILITY_LABELS: dict[str, str] = {
    "text_generation": "文本生成",
    "image_generation": "图像生成",
    "text_to_speech": "语音合成",
    "image_to_video": "图生视频",
    "text_to_video": "文生视频",
    "image_editing": "图像编辑",
    "vision_understanding": "图像理解",
    "speech_to_text": "语音识别",
}

# Key 的长度约束。上限防"整个文件粘进来"，下限挡住明显的手滑。
MIN_KEY_CHARS = 8
MAX_KEY_CHARS = 512

_MASK_BODY = "•" * 12
_HINT_CHARS = 4
_PREFIX_CHARS = 3
# 短到看不出头尾的 Key 就整条遮掉，露 4 位尾号对它来说占比太大了
_MIN_CHARS_FOR_HINT = 12


@dataclass(frozen=True, slots=True)
class CredentialView:
    """一个能力的配置状态。**不含任何密钥材料**，只有够识别的尾号。"""

    capability: str
    label: str
    configured: bool
    provider_id: str
    provider_label: str
    masked_key: str | None
    updated_at: datetime | None


def configurable_capabilities() -> tuple[str, ...]:
    """用户可以配自己 Key 的能力集合。

    两个条件取交集（ADR-025 §4）：
    - Skill 的 `model_policy.user_selectable` 允许用户改这个能力
    - 平台真的有这家 Provider 的适配器，能验证也能调用

    只满足前者的（比如 TTS 现在还没接）不该出现在设置页——
    让用户配一把配了也不会被用到的 Key，是纯粹的误导。
    """
    declared: set[str] = set()
    for spec in skill_registry.registry().specs.values():
        declared.update(spec.model_policy.user_selectable)
    return tuple(c for c in probe.PROVIDER_OF if c in declared)


def _require_capability(capability: str) -> str:
    if capability not in configurable_capabilities():
        raise AppError(
            "common.validation_failed",
            message=f"能力 {capability!r} 暂不支持自带 Key",
            detail={"allowed": list(configurable_capabilities())},
        )
    return probe.PROVIDER_OF[capability]


def _clean_key(api_key: str) -> str:
    """清洗用户粘进来的 Key。

    从网页复制往往带前后空白和换行；不清掉的话密文里就存着一个
    带 `\\n` 的 Key，上游报 401 而用户看着输入框里"明明是对的"。
    """
    key = api_key.strip()
    if not (MIN_KEY_CHARS <= len(key) <= MAX_KEY_CHARS):
        raise AppError(
            "common.validation_failed",
            message=f"API Key 长度必须在 {MIN_KEY_CHARS}–{MAX_KEY_CHARS} 之间",
        )
    if any(ch.isspace() for ch in key) or not key.isprintable():
        # 中间带空白基本是复制时框多了，直接放行会得到一个永远调不通的 Key
        raise AppError("common.validation_failed", message="API Key 里不应包含空白或控制字符")
    return key


def mask(plaintext: str) -> str:
    """尾号展示串。给人识别"这是哪一把"用，不足以重建 Key。"""
    if len(plaintext) < _MIN_CHARS_FOR_HINT:
        return _MASK_BODY
    return f"{plaintext[:_PREFIX_CHARS]}{_MASK_BODY}{plaintext[-_HINT_CHARS:]}"


def _view(capability: str, row: ProviderCredential | None) -> CredentialView:
    label = CAPABILITY_LABELS.get(capability, capability)
    if row is None:
        provider_id = probe.PROVIDER_OF[capability]
        return CredentialView(
            capability=capability,
            label=label,
            configured=False,
            provider_id=provider_id,
            provider_label=probe.PROVIDER_LABELS.get(provider_id, provider_id),
            masked_key=None,
            updated_at=None,
        )

    masked: str | None
    try:
        # 尾号只能从明文算。表里没有 hint 列，也不该有——多存一份
        # 派生自密钥的东西，就多一个要跟着轮换的地方。
        masked = mask(decrypt_secret(row.key_encrypted))
    except CryptoConfigError:
        # 加密密钥轮换过、或密文被改过。整页 500 掉是最差的处理：
        # 用户会连"哪个能力出了问题、怎么重配"都看不到。
        log.warning("credentials.mask_unavailable", capability=capability)
        masked = None
    return CredentialView(
        capability=capability,
        label=label,
        configured=True,
        provider_id=row.provider_id,
        provider_label=probe.PROVIDER_LABELS.get(row.provider_id, row.provider_id),
        masked_key=masked,
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------- 查询


async def list_for_org(db: AsyncSession, *, org_id: uuid.UUID) -> list[CredentialView]:
    """列出每个可配置能力的状态——**没配的也要列**。

    设置页要展示的是"这些能力，你分别用谁的账号"，
    只返回已配置的那几条，前端就得自己拼出"还有哪些能配"，
    那份清单迟早和后端的不一致。
    """
    rows = {r.capability: r for r in await repo.list_credentials(db, org_id=org_id)}
    return [_view(cap, rows.get(cap)) for cap in configurable_capabilities()]


async def has_own_key(db: AsyncSession, *, org_id: uuid.UUID, capability: str) -> bool:
    """这个 org 是否给这个能力配了自己的 Key。计费分支就问这一句。"""
    return await repo.get_credential(db, org_id=org_id, capability=capability) is not None


@dataclass(frozen=True, slots=True)
class ResolvedKey:
    """一次调用要用的自有 Key。**明文，只允许在内存里活到这次请求结束。**

    没有 `__repr__` 的自定义，也不需要——不要把这个对象整体丢进日志、
    异常 detail 或 `input_json`；上面那条注释就是它唯一的保护措施。
    """

    provider_id: str
    api_key: str
    capability: str


async def resolve_for_call(
    db: AsyncSession, *, org_id: uuid.UUID, capability: str
) -> ResolvedKey | None:
    """Gateway 每次调用前问的那一句：这次该用谁的 Key。

    与 :func:`has_own_key` 必须**同源同判据**——计费按"库里有没有这一行"
    给折扣，调用却按别的判据选 Key，两者一旦背离就是平台掏钱、
    用户按折扣价付款（本轮之前正是这个状态）。所以这里查的是同一张表、
    同一个查询，只是多解一次密。

    解密失败时**不回落平台 Key**：那一行还在，计费仍按 BYOK 折扣走，
    这时候拿平台 Key 顶上就是把漏洞换了个位置。抛错让用户重配。
    """
    row = await repo.get_credential(db, org_id=org_id, capability=capability)
    if row is None:
        return None
    try:
        key = decrypt_secret(row.key_encrypted)
    except CryptoConfigError as exc:
        log.warning("credentials.decrypt_failed", org_id=str(org_id), capability=capability)
        raise AppError(
            "provider.byok.rejected",
            message=f"已存的自有 Key 无法解密：{exc}",
            detail={"capability": capability, "reason": "decrypt_failed"},
        ) from exc
    return ResolvedKey(provider_id=str(row.provider_id), api_key=key, capability=capability)


# ---------------------------------------------------------------- 写入


async def put_key(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    capability: str,
    api_key: str,
) -> CredentialView:
    """新增或更换某个能力的 Key。就地覆盖，不留历史。

    不做"测过才让存"的强制：用户可能只是想先存下来，
    上游临时抽风也不该拦着他保存。测试连接是帮助，不是关卡。
    """
    provider_id = _require_capability(capability)
    key = _clean_key(api_key)

    row = await repo.upsert_credential(
        db,
        org_id=org_id,
        capability=capability,
        provider_id=provider_id,
        key_encrypted=encrypt_secret(key),
        created_by=user_id,
    )
    await db.commit()
    # 日志里只有"谁给哪个能力配了 Key"，没有 Key 本身，也没有尾号
    log.info("credentials.saved", org_id=str(org_id), capability=capability, provider=provider_id)
    return _view(capability, row)


async def delete_key(db: AsyncSession, *, org_id: uuid.UUID, capability: str) -> None:
    """移除 Key。该能力的调用与计费**自动退回平台档**——

    不需要额外的开关：`pricing.estimate` 每次都现查有没有自有 Key，
    查不到就是平台计费。多一个"是否启用 BYOK"的字段就是多一份真相，
    迟早出现"删了 Key 但开关还开着"。
    """
    _require_capability(capability)
    row = await repo.get_credential(db, org_id=org_id, capability=capability)
    if row is None:
        # 跨租户访问会落到这里：按 org_id 查不到就是不存在。
        # 返 404 不返 403——403 会确认"这东西存在，只是你没权限"。
        raise AppError("common.not_found", message="该能力还没有配置自己的 Key")

    await repo.soft_delete_credential(db, row=row)
    await db.commit()
    log.info("credentials.removed", org_id=str(org_id), capability=capability)


# ---------------------------------------------------------------- 测试连接


async def test_key(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    capability: str,
    api_key: str | None = None,
) -> probe.ProbeResult:
    """测试一把 Key 能不能用。

    `api_key` 给了就测这一把（用户刚输入、还没存库的那把），
    没给就测已存的那把——已配置的 Key 过期或被上游停用时，
    用户需要一个办法确认"是不是 Key 的问题"，而明文已经拿不回来了。
    """
    provider_id = _require_capability(capability)

    if api_key is not None:
        key = _clean_key(api_key)
    else:
        row = await repo.get_credential(db, org_id=org_id, capability=capability)
        if row is None:
            raise AppError("common.not_found", message="该能力还没有配置自己的 Key")
        try:
            key = decrypt_secret(row.key_encrypted)
        except CryptoConfigError as exc:
            # 异常信息本身不含密钥材料（crypto.py 的约定），可以直接透出
            return probe.ProbeResult(
                ok=False,
                provider_id=str(row.provider_id),
                message=f"已存的密钥无法解密：{exc}。请重新配置一次。",
                error_code="common.internal",
            )
        provider_id = str(row.provider_id)

    return await probe.verify(provider_id=provider_id, api_key=key)
