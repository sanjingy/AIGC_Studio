"""BYOK 从"设置页存下 Key"到"上游真的收到这把 Key"的整条链路（ADR-027）。

`tests/unit/test_gateway_byok_routing.py` 把 Gateway 的决策逻辑单测了，
但它把查库那一环换成了假件。这里补的正是被换掉的那一环：**真的走 HTTP
存一把 Key、真的落库加密、真的解密、真的传到适配器手里**。

上一轮的洞就长在这一环上——计费查的是"库里有没有这一行"，调用读的是
环境变量，两者中间没有任何东西把它们对上。只测其中一头都发现不了。

适配器仍然是假的：真打上游要花钱，且拿一把假 Key 去打上游只能得到 401，
证明不了"成功用它出了图"。真机验证见交付报告。
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any, ClassVar

import pytest
from httpx import AsyncClient

from adapters.providers.base import KeySource, TextRequest, TextResponse
from apps.api.core.errors import AppError
from apps.api.modules.gateway import breaker, catalog
from apps.api.modules.gateway import service as gw

pytestmark = pytest.mark.integration

BASE = "/api/v1/provider-credentials"
CAPABILITY = "text_generation"
REAL_PROVIDER = "provider.deepseek"  # text_generation 在目录里就绑这家
PLATFORM_KEY = "sk-platform-key-for-wiring-test"
ORG_KEY = "sk-org-key-0123456789abcdef"


class RecordingProvider:
    """假适配器。它是链路的终点：能在这里看到明文 Key，就说明真的传到了。"""

    provider_id = REAL_PROVIDER
    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(
        self, *, api_key: str, model_id: str, key_source: KeySource = KeySource.PLATFORM
    ) -> None:
        self._api_key = api_key
        self.model_id = model_id
        self.key_source = key_source

    async def generate_text(self, request: TextRequest) -> TextResponse:
        del request
        RecordingProvider.calls.append({"api_key": self._api_key, "key_source": self.key_source})
        return TextResponse(text='{"ok": true}', tokens_in=1, tokens_out=1, model_id=self.model_id)


@pytest.fixture(autouse=True)
def _fake_adapter(monkeypatch: pytest.MonkeyPatch) -> Any:
    """只把"发 HTTP 出去"这一步换掉，能力→Provider 的绑定保持真实。"""
    RecordingProvider.calls = []
    spec = catalog.spec_for(REAL_PROVIDER, capability=CAPABILITY)
    assert spec is not None
    monkeypatch.setattr(catalog, "SPECS", (dataclasses.replace(spec, adapter=RecordingProvider),))
    monkeypatch.setattr(catalog, "platform_key", lambda provider_id: PLATFORM_KEY)
    gw.reset_registry()
    yield
    gw.reset_registry()


async def _org_id(client: AsyncClient) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/v1/auth/me")).json()["org_id"])


async def test_stored_key_reaches_the_upstream_call(alice: AsyncClient) -> None:
    """存 Key（HTTP）→ 生成 → 上游拿到的就是这把，来源标记 org。"""
    assert (await alice.put(f"{BASE}/{CAPABILITY}", json={"api_key": ORG_KEY})).status_code == 200
    org_id = await _org_id(alice)
    await breaker.reset(breaker.scope(REAL_PROVIDER, org_id=org_id))

    await gw.generate_text(TextRequest(system="s", user="u"), org_id=org_id)

    assert RecordingProvider.calls == [{"api_key": ORG_KEY, "key_source": KeySource.ORG}]


async def test_removing_the_key_falls_back_to_platform(alice: AsyncClient) -> None:
    """移除后立刻回落平台默认——不需要重启进程，也不需要等缓存过期。"""
    assert (await alice.put(f"{BASE}/{CAPABILITY}", json={"api_key": ORG_KEY})).status_code == 200
    org_id = await _org_id(alice)
    await breaker.reset(breaker.scope(REAL_PROVIDER, org_id=org_id))
    await breaker.reset(REAL_PROVIDER)

    await gw.generate_text(TextRequest(system="s", user="u"), org_id=org_id)
    assert (await alice.delete(f"{BASE}/{CAPABILITY}")).status_code == 204
    await gw.generate_text(TextRequest(system="s", user="u"), org_id=org_id)

    assert [c["key_source"] for c in RecordingProvider.calls] == [
        KeySource.ORG,
        KeySource.PLATFORM,
    ]
    assert RecordingProvider.calls[1]["api_key"] == PLATFORM_KEY


async def test_other_tenant_never_gets_this_key(alice: AsyncClient, bob: AsyncClient) -> None:
    """另一个租户走的是平台档。凭证查询按 org_id 过滤，这里钉死它。"""
    assert (await alice.put(f"{BASE}/{CAPABILITY}", json={"api_key": ORG_KEY})).status_code == 200
    bob_org = await _org_id(bob)
    await breaker.reset(REAL_PROVIDER)

    await gw.generate_text(TextRequest(system="s", user="u"), org_id=bob_org)

    assert RecordingProvider.calls[0]["api_key"] == PLATFORM_KEY


async def test_billing_discount_and_actual_key_agree(alice: AsyncClient, db: Any) -> None:
    """计费判"走折扣"与 Gateway 判"用自有 Key"必须同进同退。

    这两个判断分家正是本轮要修的洞：计费按库里那一行给了折扣，
    调用却用平台 Key 出的图，差额平台自己吃。
    """
    from apps.api.modules.billing import pricing

    org_id = await _org_id(alice)
    await breaker.reset(breaker.scope(REAL_PROVIDER, org_id=org_id))
    await breaker.reset(REAL_PROVIDER)

    assert (await pricing.uses_own_key(db, org_id=org_id, capability=CAPABILITY)) is False
    await gw.generate_text(TextRequest(system="s", user="u"), org_id=org_id)
    assert RecordingProvider.calls[-1]["key_source"] is KeySource.PLATFORM

    assert (await alice.put(f"{BASE}/{CAPABILITY}", json={"api_key": ORG_KEY})).status_code == 200
    assert (await pricing.uses_own_key(db, org_id=org_id, capability=CAPABILITY)) is True
    await gw.generate_text(TextRequest(system="s", user="u"), org_id=org_id)
    assert RecordingProvider.calls[-1]["key_source"] is KeySource.ORG


async def test_undecryptable_key_does_not_silently_use_platform(
    alice: AsyncClient, db: Any
) -> None:
    """密文坏了（密钥轮换过、被人改过）→ 报错，不偷偷用平台 Key。

    那一行还在，计费仍按 BYOK 折扣走；这时候平台 Key 顶上去，
    洞只是换了个位置。
    """
    from sqlalchemy import select

    from apps.api.modules.billing.models import ProviderCredential

    assert (await alice.put(f"{BASE}/{CAPABILITY}", json={"api_key": ORG_KEY})).status_code == 200
    org_id = await _org_id(alice)
    row = (
        await db.execute(
            select(ProviderCredential).where(
                ProviderCredential.org_id == org_id,
                ProviderCredential.capability == CAPABILITY,
            )
        )
    ).scalar_one()
    row.key_encrypted = "这不是一段合法密文"
    await db.commit()

    with pytest.raises(AppError) as exc:
        await gw.generate_text(TextRequest(system="s", user="u"), org_id=org_id)

    assert exc.value.code == "provider.byok.rejected"
    assert RecordingProvider.calls == []
