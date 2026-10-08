"""已保存连接把文本模型从 Chat 改成 Responses（USER_FLOW_REPAIR responses）。

用户场景：cchost + `gpt-6.1-sol` 原先存成 `openai_chat`，生成回 404；CC Switch 里这条供应商的
上游格式是 OpenAI Responses。要求：在「模型」页改协议即可，**不用重新输入 Key**；其余连接
（包括没改的 Chat 连接）不被静默重写；改完后 Gateway 真的按 Responses 造适配器，指向它的
组织默认不失效。

真的走 HTTP 存、真的落库、真的被 `_resolve` 读出来；不打任何上游（地址用 `.invalid`，
测试连接在 `ENV=test` 下走 Mock 探测，只造适配器不发请求）。
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from adapters.providers.base import KeySource
from adapters.providers.openai_compat import OpenAICompatTextProvider
from adapters.providers.openai_responses import OpenAIResponsesTextProvider
from apps.api.core.crypto import decrypt_secret
from apps.api.core.db import session_scope
from apps.api.modules.gateway import service as gw

pytestmark = pytest.mark.integration

BASE = "/api/v1/model-config"
CONN = f"{BASE}/connections"
TEXT = "text_generation"
KEY = "sk-cchost-like-key-0123456789abcd"
OTHER_KEY = "sk-other-chat-key-abcdef9876543210"
MODEL = "gpt-6.1-sol"
URL = "https://relay.example.invalid/v1"


async def _org(client: AsyncClient) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/v1/auth/me")).json()["org_id"])


async def _stored(connection_id: str) -> tuple[bytes, list[dict]]:
    async with session_scope() as db:
        row = (
            await db.execute(
                text("SELECT key_encrypted, models FROM org_provider_connections WHERE id = :i"),
                {"i": uuid.UUID(connection_id)},
            )
        ).one()
    return row[0], row[1]


async def test_protocol_catalog_lists_responses_as_text(alice: AsyncClient) -> None:
    body = (await alice.get(f"{BASE}/presets")).json()
    protocols = {p["protocol"]: p for p in body["protocols"]}
    assert protocols["openai_responses"]["capability"] == TEXT
    assert protocols["openai_responses"]["consistency_verified"] is None
    assert protocols["openai_chat"]["capability"] == TEXT


async def test_switch_saved_chat_model_to_responses_keeps_key_and_default(
    alice: AsyncClient,
) -> None:
    # 一条照线上形状存下的 Chat 连接 + 一条不相干的 Chat 连接
    created = await alice.post(
        CONN,
        json={
            "label": "relay.example.invalid",
            "base_url": f"{URL}/chat/completions",
            "models": [{"model_id": MODEL, "protocol": "openai_chat"}],
            "api_key": KEY,
        },
    )
    assert created.status_code == 201, created.text
    conn = created.json()
    other = (
        await alice.post(
            CONN,
            json={
                "label": "另一家",
                "base_url": "https://other.example.invalid/v1",
                "models": [{"model_id": "chat-m", "protocol": "openai_chat"}],
                "api_key": OTHER_KEY,
            },
        )
    ).json()
    key_before, _ = await _stored(conn["id"])
    other_before = await _stored(other["id"])

    ok = await alice.put(
        f"{BASE}/{TEXT}",
        json={"provider_id": conn["provider_id"], "model_id": MODEL, "key_source": "org"},
    )
    assert ok.status_code == 200
    org = await _org(alice)
    (route,) = (await gw._resolve(TEXT, org_id=org)).routes
    assert type(route.factory()) is OpenAICompatTextProvider  # 改之前：旧行为不变

    # 只改协议，不带 Key、不带地址（前端 patchBody 的形状）
    patched = await alice.patch(
        f"{CONN}/{conn['id']}",
        json={"models": [{"model_id": MODEL, "protocol": "openai_responses", "reasoning": False}]},
    )
    assert patched.status_code == 200, patched.text
    view = patched.json()
    assert KEY not in patched.text
    assert [(m["model_id"], m["protocol"], m["capability"]) for m in view["models"]] == [
        (MODEL, "openai_responses", TEXT)
    ]
    assert view["masked_key"] == conn["masked_key"]
    assert view["base_url"] == URL

    key_after, models_after = await _stored(conn["id"])
    assert key_after == key_before and decrypt_secret(key_after) == KEY
    assert models_after == [{"model_id": MODEL, "protocol": "openai_responses", "reasoning": False}]
    # 不相干的连接一字未动
    assert await _stored(other["id"]) == other_before

    # 组织默认仍指向这个连接与模型，Gateway 现在按 Responses 造适配器、拿的仍是原 Key
    sel = next(i for i in (await alice.get(BASE)).json()["items"] if i["capability"] == TEXT)[
        "selection"
    ]
    assert (sel["provider_id"], sel["model_id"], sel["broken_reason"]) == (
        conn["provider_id"],
        MODEL,
        None,
    )
    (route,) = (await gw._resolve(TEXT, org_id=org)).routes
    adapter = route.factory()
    assert type(adapter) is OpenAIResponsesTextProvider
    assert (adapter.model_id, adapter._base_url, adapter._api_key) == (MODEL, URL, KEY)
    assert route.key_source is KeySource.ORG

    # 已保存连接按新协议测试连接（ENV=test 走 Mock 探测），不需要 Key
    tested = await alice.post(
        f"{CONN}/{conn['id']}/test", json={"protocol": "openai_responses", "model_id": MODEL}
    )
    assert tested.status_code == 200 and tested.json()["ok"] is True
    assert KEY not in tested.text

    # 改回 Chat 同样只动协议
    back = await alice.patch(
        f"{CONN}/{conn['id']}",
        json={"models": [{"model_id": MODEL, "protocol": "openai_chat", "reasoning": False}]},
    )
    assert back.status_code == 200
    assert decrypt_secret((await _stored(conn["id"]))[0]) == KEY


async def test_new_responses_connection_normalizes_a_responses_url(alice: AsyncClient) -> None:
    created = await alice.post(
        CONN,
        json={
            "label": "relay.example.invalid",
            "base_url": f"{URL}/responses",
            "models": [{"model_id": MODEL, "protocol": "openai_responses"}],
            "api_key": KEY,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["base_url"] == URL
    # 草稿测试连接也接受完整的 Responses 地址
    draft = await alice.post(
        f"{CONN}/test",
        json={
            "protocol": "openai_responses",
            "model_id": MODEL,
            "base_url": f"{URL}/responses",
            "api_key": KEY,
        },
    )
    assert draft.status_code == 200 and draft.json()["ok"] is True
    assert KEY not in draft.text


async def test_responses_model_cannot_be_an_image_default(alice: AsyncClient) -> None:
    conn = (
        await alice.post(
            CONN,
            json={
                "label": "relay.example.invalid",
                "base_url": URL,
                "models": [{"model_id": MODEL, "protocol": "openai_responses"}],
                "api_key": KEY,
            },
        )
    ).json()
    wrong = await alice.put(
        f"{BASE}/image_generation",
        json={"provider_id": conn["provider_id"], "model_id": None, "key_source": "org"},
    )
    assert wrong.status_code == 400
