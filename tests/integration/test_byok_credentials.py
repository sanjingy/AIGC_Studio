"""BYOK 的 service + API（ADR-025）。

数据层的用例在 `test_byok_schema.py`，那边只验表；这里验的是**行为**：
存取全链路、测试连接的两种结论、删除后计费回退、跨租户 404。

贯穿全篇的一条断言：**响应体里任何地方都不能出现明文 Key**。
它不是某一个用例的检查项，而是每次请求之后都要过一遍的红线——
所以做成 `_assert_no_plaintext`，谁新增用例都得顺手带上。
"""

from __future__ import annotations

import json
import uuid

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.crypto import decrypt_secret
from apps.api.modules.billing import credentials
from apps.api.modules.billing import service as billing_service
from apps.api.modules.billing.models import ProviderCredential

pytestmark = pytest.mark.integration

BASE = "/api/v1/provider-credentials"

# Mock 探测器的规则：sk- 开头且不短于 20 字符视为可用（见 gateway/probe.py）
GOOD_KEY = "sk-byok-test-0123456789abcdef"
BAD_KEY = "not-a-real-key-at-all"


def _assert_no_plaintext(resp: Response, *keys: str) -> None:
    body = resp.text
    for key in keys:
        assert key not in body, f"响应体里出现了明文 Key：{resp.request.url}"
        # 只回显一半也是泄露：尾号 4 位是刻意留的，8 位片段就不行了
        assert key[:12] not in body
        assert key[8:20] not in body


async def _put(client: AsyncClient, capability: str, key: str) -> Response:
    return await client.put(f"{BASE}/{capability}", json={"api_key": key})


# ---------------------------------------------------------------- 存取


async def test_lists_every_configurable_capability_even_when_unset(alice: AsyncClient) -> None:
    """没配过也要列出来——设置页要展示"这些能力你分别用谁的账号"。"""
    r = await alice.get(BASE)
    assert r.status_code == 200, r.text

    items = r.json()["items"]
    assert {i["capability"] for i in items} == set(credentials.configurable_capabilities())
    for item in items:
        assert item["configured"] is False
        assert item["masked_key"] is None
        assert item["updated_at"] is None
        # 未配置也要给 provider 与中文名，否则前端只能显示原始枚举
        assert item["provider_id"] and item["label"] and item["provider_label"]


async def test_saved_key_is_encrypted_and_never_echoed(
    alice: AsyncClient, db: AsyncSession
) -> None:
    r = await _put(alice, "text_generation", GOOD_KEY)
    assert r.status_code == 200, r.text
    _assert_no_plaintext(r, GOOD_KEY)

    body = r.json()
    assert body["configured"] is True
    assert body["provider_id"] == "provider.deepseek"
    # 尾号够识别"这是哪一把"，又不足以重建
    assert body["masked_key"].endswith(GOOD_KEY[-4:])
    assert GOOD_KEY[4:-4] not in body["masked_key"]

    listing = await alice.get(BASE)
    _assert_no_plaintext(listing, GOOD_KEY)
    text_row = next(i for i in listing.json()["items"] if i["capability"] == "text_generation")
    assert text_row["configured"] is True
    assert text_row["masked_key"] == body["masked_key"]

    # 库里存的是密文，且能解回明文——解密只发生在服务端内存里
    org_id = uuid.UUID((await alice.get("/api/v1/auth/me")).json()["org_id"])
    stored = (
        await db.execute(
            select(ProviderCredential).where(
                ProviderCredential.org_id == org_id,
                ProviderCredential.capability == "text_generation",
            )
        )
    ).scalar_one()
    assert GOOD_KEY not in stored.key_encrypted
    assert decrypt_secret(stored.key_encrypted) == GOOD_KEY


async def test_replacing_a_key_updates_in_place(alice: AsyncClient, db: AsyncSession) -> None:
    """换 Key 必须是 UPDATE。

    唯一约束 `(org_id, capability)` **不排除软删行**，做成
    "软删旧的再插新的"会在第二次换 Key 时撞约束——用户会看到一个 500。
    """
    second = "sk-byok-test-replacement-9999"
    first = await _put(alice, "text_generation", GOOD_KEY)
    replaced = await _put(alice, "text_generation", second)
    assert replaced.status_code == 200, replaced.text
    _assert_no_plaintext(replaced, second, GOOD_KEY)
    assert replaced.json()["masked_key"] != first.json()["masked_key"]
    assert replaced.json()["masked_key"].endswith(second[-4:])

    org_id = uuid.UUID((await alice.get("/api/v1/auth/me")).json()["org_id"])
    rows = list(
        (
            await db.execute(select(ProviderCredential).where(ProviderCredential.org_id == org_id))
        ).scalars()
    )
    assert len(rows) == 1, "换 Key 插了新行，唯一约束迟早会炸"
    assert decrypt_secret(rows[0].key_encrypted) == second


# ---------------------------------------------------------------- 测试连接


async def test_probe_reports_success_with_a_concrete_message(alice: AsyncClient) -> None:
    r = await alice.post(f"{BASE}/text_generation/test", json={"api_key": GOOD_KEY})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["message"], "成功也要给一句话，光一个勾说明不了测到了什么"
    _assert_no_plaintext(r, GOOD_KEY)


async def test_probe_failure_surfaces_upstream_reason_without_the_key(
    alice: AsyncClient,
) -> None:
    """失败必须说清原因，但上游回显的 Key 要抹掉。

    Mock 探测器故意把 Key 贴进错误文案里（真实上游就是这么干的），
    这个用例守的就是那道脱敏。
    """
    r = await alice.post(f"{BASE}/image_generation/test", json={"api_key": BAD_KEY})
    assert r.status_code == 200, "Key 不可用是一个测试结论，不是接口失败"

    body = r.json()
    assert body["ok"] is False
    assert body["error_code"] == "provider.account.insufficient"
    assert "Authentication Fails" in body["message"], "不能把原因糊成一句'连接失败'"
    _assert_no_plaintext(r, BAD_KEY)


async def test_probe_can_test_the_stored_key(alice: AsyncClient) -> None:
    """不传 api_key 就测已存的那把——存完之后前端再也拿不到明文。"""
    await _put(alice, "text_generation", GOOD_KEY)
    r = await alice.post(f"{BASE}/text_generation/test", json={})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    _assert_no_plaintext(r, GOOD_KEY)


async def test_probe_without_stored_key_is_404(alice: AsyncClient) -> None:
    r = await alice.post(f"{BASE}/image_generation/test", json={})
    assert r.status_code == 404


async def test_unsupported_capability_is_rejected(alice: AsyncClient) -> None:
    """没有适配器的能力不能配 Key——配了也不会被用到，是纯误导。"""
    r = await _put(alice, "speech_to_text", GOOD_KEY)
    assert r.status_code == 422
    assert "speech_to_text" in r.json()["error"]["message"]


# ---------------------------------------------------------------- 删除与回退


async def test_delete_requires_an_existing_key(alice: AsyncClient) -> None:
    await _put(alice, "image_generation", GOOD_KEY)

    gone = await alice.delete(f"{BASE}/image_generation")
    assert gone.status_code == 204

    listing = (await alice.get(BASE)).json()["items"]
    assert all(not i["configured"] for i in listing)

    again = await alice.delete(f"{BASE}/image_generation")
    assert again.status_code == 404, "重复删除应当是 404，不是静默成功"


# ---------------------------------------------------------------- 计费联动（ADR-025）


async def _estimate(client: AsyncClient) -> int:
    r = await client.post(
        "/api/v1/credits/estimate",
        json={"type": "image.generate", "input": {"n": 1}},
    )
    assert r.status_code == 200, r.text
    return int(r.json()["estimated_credits"])


async def test_own_key_skips_provider_markup_and_delete_restores_it(
    alice: AsyncClient, db: AsyncSession
) -> None:
    """配了自己的 Key，估价跳过 provider_cost 加价，只收隐性成本档。

    不是免费：完全免费等于邀请用户拿低价 Key 无限占用平台存储与计算，
    免费额度设计会被绕过（ADR-025 §1）。
    """
    cfg = await billing_service.rules(db)
    platform = await _estimate(alice)

    await _put(alice, "image_generation", GOOD_KEY)
    byok = await _estimate(alice)

    expected = cfg["byok_unit_credits"] * cfg["image_retry_factor"] // 100
    assert byok == expected, "BYOK 档应当只收 pricing_rules 里的隐性成本"
    assert 0 < byok < platform, "既不能免费，也不该还按平台售价收"

    # 文本能力没配 Key，不该被图像的 Key 带着一起打折
    text_estimate = await alice.post(
        "/api/v1/credits/estimate", json={"type": "mock.echo", "input": {}}
    )
    assert text_estimate.status_code == 200

    await alice.delete(f"{BASE}/image_generation")
    assert await _estimate(alice) == platform, "移除 Key 后必须原样退回平台计费"


# ---------------------------------------------------------------- 租户隔离


async def test_other_tenant_cannot_see_or_delete_the_key(
    alice: AsyncClient, bob: AsyncClient
) -> None:
    """跨租户一律 404。403 会确认"这东西存在，只是你没权限"，可被枚举。"""
    await _put(alice, "text_generation", GOOD_KEY)

    listing = await bob.get(BASE)
    _assert_no_plaintext(listing, GOOD_KEY)
    assert all(not i["configured"] for i in listing.json()["items"])

    assert (await bob.delete(f"{BASE}/text_generation")).status_code == 404
    assert (await bob.post(f"{BASE}/text_generation/test", json={})).status_code == 404

    # alice 那边不受影响
    mine = (await alice.get(BASE)).json()["items"]
    assert next(i for i in mine if i["capability"] == "text_generation")["configured"] is True


async def test_key_never_appears_in_any_response(alice: AsyncClient) -> None:
    """把这条单独立一个用例：它是本功能的第一红线。"""
    responses = [
        await _put(alice, "text_generation", GOOD_KEY),
        await alice.get(BASE),
        await alice.post(f"{BASE}/text_generation/test", json={"api_key": GOOD_KEY}),
        await alice.post(f"{BASE}/text_generation/test", json={}),
    ]
    for resp in responses:
        _assert_no_plaintext(resp, GOOD_KEY)
        # 连字段名都不该出现——有 api_key 字段就说明有人往回吐了
        assert "api_key" not in json.dumps(resp.json() if resp.content else {})
