"""用户自带 Key 的"测试连接"（ADR-025）。

这条路径与 `service.py` 的能力路由是**两回事**，所以单独一个文件：
路由表解析的是"平台的 Key 该用哪家"，这里验证的是"用户刚粘进来的这把
Key 能不能用"——Key 还没入库，也不该进任何注册表。

三条规则：

1. **探测必须免费**。具体到每家怎么探，写在各自 adapter 的 `verify_key()` 里。
2. **失败要说清原因**。"连接失败"对用户毫无用处，他分不清是 Key 打错了、
   上游欠费了还是网络不通，只能反复重试。
3. **上游回显的密钥材料必须抹掉**。上游报错时把 Key 原样贴回来是常见做法，
   原样透出去等于把它写进了浏览器控制台、前端日志和用户的截图。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from adapters.providers.base import KeySource, KeyVerifier
from apps.api.core.errors import AppError
from apps.api.core.logging import get_logger
from apps.api.modules.gateway import catalog

log = get_logger(__name__)

# 能力 → 由哪家提供 / Provider 的展示名。**都来自 catalog，不在这里另写一份**：
# 设置页按这份映射让用户配 Key，Gateway 按同一份映射选适配器去调用，
# 两边分家的下场是"设置页能配、真调用时说没有这家 Provider"。
PROVIDER_OF: dict[str, str] = catalog.provider_of()
PROVIDER_LABELS: dict[str, str] = catalog.labels()

MAX_MESSAGE_CHARS = 300

# 密钥形状的串。上游报错里回显 Key 时通常连前缀一起贴回来。
_KEY_SHAPED = re.compile(r"\bsk-[A-Za-z0-9_\-]{4,}", re.IGNORECASE)
_MASK = "***"

# 一段多长的密钥片段算"泄露"。太短会把正常单词误伤成 ***，
# 太长则挡不住被截断的回显。
_LEAK_WINDOW = 8


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """探测结论。`message` 已脱敏，可以直接给用户看。"""

    ok: bool
    provider_id: str
    message: str
    error_code: str | None = None


def redact(text: str, secret: str) -> str:
    """抹掉文本里的密钥材料，并截断到可展示长度。

    三层，缺一不可：
    - 整把 Key 的精确匹配
    - 任何 `sk-` 开头的长串（用户配的可能不是我们提交的那把）
    - Key 的任意 8 字符片段——上游常只回显前后几位，精确匹配挡不住
    """
    out = text
    if secret:
        out = out.replace(secret, _MASK)
        for i in range(len(secret) - _LEAK_WINDOW + 1):
            window = secret[i : i + _LEAK_WINDOW]
            if window in out:
                out = out.replace(window, _MASK)
    out = _KEY_SHAPED.sub(_MASK, out)
    return out[:MAX_MESSAGE_CHARS]


class Prober(Protocol):
    async def probe(self, *, provider_id: str, api_key: str) -> str: ...


class LiveProber:
    """真打上游。只在有 Key 且非测试环境下使用。"""

    async def probe(self, *, provider_id: str, api_key: str) -> str:
        return await _verifier(provider_id, api_key).verify_key()


class MockProber:
    """确定性 Mock。

    规则写死成一句话：**以 `sk-` 开头且不短于 20 字符视为可用**。
    不确定的 Mock 会让测试时红时绿，比没有 Mock 更糟。

    失败文案里**故意带上这把 Key** ——真实上游就是这么干的
    （DeepSeek 的 401 会把收到的 key 贴回来）。脱敏统一在
    :func:`verify` 里做，Mock 这么写正好把那道防线钉在测试里。
    """

    async def probe(self, *, provider_id: str, api_key: str) -> str:
        del provider_id
        if api_key.startswith("sk-") and len(api_key) >= 20:
            return "鉴权通过（Mock）"
        raise AppError(
            "provider.account.insufficient",
            message=f"Authentication Fails, Your api key: {api_key} is invalid",
        )


_prober: Prober | None = None


def set_prober(prober: Prober | None) -> None:
    global _prober
    _prober = prober


def get_prober() -> Prober:
    """选探测实现。

    `ENV=test` 一律 Mock，理由同 `agent/llm.py` 的 `get_provider()`：
    测试环境有 Key 时，漏设一次就是拿用户的真 Key 去打真实上游。
    安全默认必须写在生产代码里，不能靠 conftest 记得设。
    """
    global _prober
    if _prober is None:
        from apps.api.core.config import get_settings

        _prober = MockProber() if get_settings().env == "test" else LiveProber()
        log.info("probe.prober_selected", kind=type(_prober).__name__)
    return _prober


def _verifier(provider_id: str, api_key: str) -> KeyVerifier:
    """按 provider_id 构造验证器。

    目录里现取现造，不预先建表：`{id: lambda}` 那种表捕获的是变量不是值，
    这个仓库已经因此让 DeepSeek 拿着万相的 Key 去请求过一次。

    来源固定标成 ORG——探测这条路径上的 Key 永远是用户的，
    平台自己的 Key 不需要测试连接。
    """
    spec = catalog.spec_for(provider_id)
    if spec is None:
        raise AppError("common.validation_failed", message=f"不支持的 Provider {provider_id!r}")
    verifier: KeyVerifier = spec.adapter(api_key=api_key, key_source=KeySource.ORG)
    return verifier


async def verify(*, provider_id: str, api_key: str) -> ProbeResult:
    """验证一把还没入库的 Key。

    永远返回结论而不是抛错——"这把 Key 不能用"是一个正常的测试结果，
    不是接口调用失败。把它抛成 4xx 会让前端要在错误分支里区分
    "网络断了"和"Key 错了"，那本来就是这个接口该替它分好的。
    """
    try:
        message = await get_prober().probe(provider_id=provider_id, api_key=api_key)
    except AppError as exc:
        # 日志只记错误码：message 里可能带着上游回显的密钥材料
        log.info("probe.failed", provider=provider_id, code=exc.code)
        # message 没给时 AppError 会把它填成错误码，那对用户没有意义，
        # 这种情况退回目录里的用户文案。
        raw = exc.spec.user_message if exc.message == exc.code else exc.message
        return ProbeResult(
            ok=False,
            provider_id=provider_id,
            message=redact(raw, api_key),
            error_code=exc.code,
        )
    return ProbeResult(ok=True, provider_id=provider_id, message=redact(message, api_key))
