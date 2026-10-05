"""模型上游配置的整条链路（CONFIG_STAGE_UPLOAD_0924 · A；MULTI_PROVIDER_A1 · ADR-039）。

HTTP 存配置 → 落库 → Gateway 解析 → 计费判断。

单测（`tests/unit/test_upstream_selection.py`）把查库那几处换成了假件；
这里补的正是被换掉的那一段：**真的走 HTTP 存、真的落 `org_model_defaults` /
`org_provider_connections`、真的被 `_resolve` 读出来、计费用的是同一个结论**。

不打任何上游：连接只构造适配器、不发请求；测试连接在 `ENV=test` 下走 Mock 探测；
出网地址只做形状校验。连接地址一律用保留域名 `.invalid`（RFC 2606）——共用库的
Worker 容器可能捡到这里建的出图任务，它解析不出来就失败，不会把请求发到任何真实主机。
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from httpx import AsyncClient
from sqlalchemy import text

from adapters.providers.base import KeySource
from adapters.providers.openai_compat import OpenAICompatTextProvider
from adapters.providers.openai_images import OpenAIImagesProvider
from apps.api.core.config import get_settings
from apps.api.core.crypto import decrypt_secret, encrypt_secret
from apps.api.core.db import dispose_engine, session_scope
from apps.api.core.errors import AppError
from apps.api.modules.billing import pricing
from apps.api.modules.gateway import catalog
from apps.api.modules.gateway import service as gw

pytestmark = pytest.mark.integration

BASE = "/api/v1/model-config"
CONN = f"{BASE}/connections"
TEXT = "text_generation"
IMAGE = "image_generation"
ORG = catalog.ORG_PROVIDER_PREFIX
LEGACY = catalog.LEGACY_CUSTOM_TEXT_PROVIDER_ID
ENDPOINT_KEY = "sk-custom-endpoint-secret-0123456789"
IMAGE_KEY = "sk-image-connection-secret-abcdef987"
NEW_KEY = "sk-rotated-endpoint-secret-zyxwvu5555"
DEEPSEEK_KEY = "sk-deepseek-org-key-abcdef0123456"
TEXT_URL = "https://llm.example.invalid/v1"
IMAGE_URL = "https://img.example.invalid/v1"

#: 迁移测试降到的那一版：`org_text_endpoints` 还在、连接表还没建
PRE_CONNECTIONS = "3a9d2c7e5b10"


async def _org(client: AsyncClient) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/v1/auth/me")).json()["org_id"])


def _item(body: dict, capability: str) -> dict:
    return next(i for i in body["items"] if i["capability"] == capability)


async def _text_connection(client: AsyncClient, *, model: str = "qwen2.5-72b-instruct") -> dict:
    resp = await client.post(
        CONN,
        json={
            "label": "公司网关",
            "base_url": TEXT_URL + "/",
            "models": [{"model_id": model, "protocol": "openai_chat"}],
            "api_key": ENDPOINT_KEY,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _image_connection(client: AsyncClient) -> dict:
    resp = await client.post(
        CONN,
        json={
            "preset_id": "zhipu_cogview",
            "base_url": IMAGE_URL,
            "api_key": IMAGE_KEY,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ------------------------------------------------------------------ 目录 / 平台默认


async def test_catalog_shape_and_platform_default(alice: AsyncClient) -> None:
    body = (await alice.get(BASE)).json()
    text_item, image_item = _item(body, TEXT), _item(body, IMAGE)

    providers = {p["provider_id"]: p for p in text_item["providers"]}
    assert providers["provider.deepseek"]["available"] is True
    assert [m["model_id"] for m in providers["provider.deepseek"]["models"]] == list(
        catalog.model_ids(TEXT, "provider.deepseek")
    )
    # 没有连接时只有目录里的一家；旧的"一个自定义端点"占位选项已经没了
    assert list(providers) == ["provider.deepseek"]
    assert "custom_endpoint" not in text_item
    assert text_item["selection"]["layer"] == "platform"
    assert text_item["selection"]["broken_reason"] is None
    # 两个能力都能指向自带 Key 的连接（由协议白名单推出）
    assert text_item["supports_org_connections"] is True
    assert image_item["supports_org_connections"] is True

    (dashscope,) = image_item["providers"]
    assert dashscope["provider_id"] == "provider.dashscope" and dashscope["kind"] == "catalog"
    # 平台万相路由不在这个字段上表态
    assert dashscope["consistency_verified"] is None

    pending = [i for i in body["items"] if not i["available"]]
    assert all(i["providers"] == [] for i in pending)


async def test_mismatched_model_and_provider_is_rejected(alice: AsyncClient) -> None:
    resp = await alice.put(
        f"{BASE}/{IMAGE}",
        json={
            "provider_id": "provider.dashscope",
            "model_id": "deepseek-chat",
            "key_source": "platform",
        },
    )
    assert resp.status_code == 400
    resp = await alice.put(
        f"{BASE}/{IMAGE}",
        json={"provider_id": "provider.deepseek", "model_id": None, "key_source": "platform"},
    )
    assert resp.status_code == 400
    assert _item((await alice.get(BASE)).json(), IMAGE)["selection"]["layer"] == "platform"


async def test_own_key_billing_requires_a_saved_key(alice: AsyncClient) -> None:
    resp = await alice.put(
        f"{BASE}/{IMAGE}",
        json={"provider_id": "provider.dashscope", "model_id": None, "key_source": "org"},
    )
    assert resp.status_code == 422


async def test_image_selection_persists_and_drives_resolution(alice: AsyncClient) -> None:
    resp = await alice.put(
        f"{BASE}/{IMAGE}",
        json={
            "provider_id": "provider.dashscope",
            "model_id": "wan2.2-t2i-plus",
            "key_source": "platform",
        },
    )
    assert resp.status_code == 200
    # 刷新（重新 GET）仍是保存值
    sel = _item((await alice.get(BASE)).json(), IMAGE)["selection"]
    assert (sel["provider_id"], sel["model_id"], sel["key_source"], sel["layer"]) == (
        "provider.dashscope",
        "wan2.2-t2i-plus",
        "platform",
        "org",
    )


async def test_removing_key_flips_org_billing_back_to_platform(alice: AsyncClient) -> None:
    assert (
        await alice.put(
            f"/api/v1/provider-credentials/{TEXT}?provider_id=provider.deepseek",
            json={"api_key": DEEPSEEK_KEY},
        )
    ).status_code == 200
    ok = await alice.put(
        f"{BASE}/{TEXT}",
        json={
            "provider_id": "provider.deepseek",
            "model_id": "deepseek-v4-flash",
            "key_source": "org",
        },
    )
    assert ok.status_code == 200
    org = await _org(alice)
    async with session_scope() as db:
        assert await pricing.uses_own_key(db, org_id=org, capability=TEXT) is True

    assert (await alice.delete(f"/api/v1/provider-credentials/{TEXT}")).status_code == 204
    sel = _item((await alice.get(BASE)).json(), TEXT)["selection"]
    assert (sel["key_source"], sel["model_id"]) == ("platform", "deepseek-v4-flash")
    async with session_scope() as db:
        assert await pricing.uses_own_key(db, org_id=org, capability=TEXT) is False


# ------------------------------------------------------------------ 预设


async def test_presets_are_facts_without_prices(alice: AsyncClient) -> None:
    resp = await alice.get(f"{BASE}/presets")
    assert resp.status_code == 200
    body = resp.json()
    by_id = {p["preset_id"]: p for p in body["presets"]}
    assert {"deepseek", "zhipu", "volcengine_seedream", "zhipu_cogview"} <= set(by_id)
    assert "price" not in resp.text and "credits" not in resp.text

    protocols = {p["protocol"]: p for p in body["protocols"]}
    assert protocols["openai_chat"]["capability"] == TEXT
    assert protocols["openai_images"]["capability"] == IMAGE
    # ADR-039 第 7 条：第三方出图实测前一律"未实测"；文本协议不涉及出图
    assert protocols["openai_images"]["consistency_verified"] is False
    assert protocols["openai_chat"]["consistency_verified"] is None

    seedream = by_id["volcengine_seedream"]
    assert seedream["capabilities"] == [IMAGE]
    assert all(m["consistency_verified"] is False for m in seedream["models"])
    assert all(m["consistency_verified"] is None for m in by_id["deepseek"]["models"])
    # 自定义中转：没有地址、没有模型，但协议明确
    relay = by_id["openai_images_relay"]
    assert (relay["base_url"], relay["models"], relay["protocols"]) == ("", [], ["openai_images"])


# ------------------------------------------------------------------ 连接：保存时校验


async def test_private_base_url_is_rejected_on_save(alice: AsyncClient) -> None:
    for url in (
        "https://169.254.169.254/v1",
        "https://127.0.0.1/v1",
        "https://10.0.0.8/v1",
        "https://localhost/v1",
        "http://api.example.invalid/v1",
        "https://u:p@example.invalid/v1",
    ):
        resp = await alice.post(
            CONN,
            json={
                "label": "x",
                "base_url": url,
                "models": [{"model_id": "m", "protocol": "openai_chat"}],
                "api_key": ENDPOINT_KEY,
            },
        )
        assert resp.status_code == 422, url
    # 预设地址也一样能改，改坏了一样拒
    resp = await alice.post(
        CONN,
        json={"preset_id": "deepseek", "base_url": "https://192.168.1.1", "api_key": ENDPOINT_KEY},
    )
    assert resp.status_code == 422
    # 改已有连接的地址同样过校验
    conn = await _text_connection(alice)
    bad = await alice.patch(f"{CONN}/{conn['id']}", json={"base_url": "https://[::1]/v1"})
    assert bad.status_code == 422
    assert (await alice.get(CONN)).json()["items"][0]["base_url"] == TEXT_URL


async def test_protocol_outside_whitelist_is_rejected(alice: AsyncClient) -> None:
    for protocol in ("dashscope_qwen_image", "tts", "openai_audio", ""):
        resp = await alice.post(
            CONN,
            json={
                "label": "x",
                "base_url": TEXT_URL,
                "models": [{"model_id": "m", "protocol": protocol}],
                "api_key": ENDPOINT_KEY,
            },
        )
        assert resp.status_code == 422, protocol
    assert (await alice.get(CONN)).json()["items"] == []
    # 保存前测试也不接受白名单外的协议
    probe = await alice.post(
        f"{CONN}/test",
        json={
            "protocol": "kling_image",
            "model_id": "m",
            "base_url": TEXT_URL,
            "api_key": IMAGE_KEY,
        },
    )
    assert probe.status_code == 422


async def test_unknown_preset_is_rejected(alice: AsyncClient) -> None:
    resp = await alice.post(CONN, json={"preset_id": "no_such_vendor", "api_key": ENDPOINT_KEY})
    assert resp.status_code == 422


async def test_connection_limit_comes_from_settings(alice: AsyncClient) -> None:
    previous = os.environ.get("ORG_PROVIDER_CONNECTION_LIMIT")
    # 赋值 + 清缓存（CLAUDE.md：compose 注入过的变量 setdefault 不生效）
    os.environ["ORG_PROVIDER_CONNECTION_LIMIT"] = "2"
    get_settings.cache_clear()
    try:
        first = await _text_connection(alice, model="m1")
        await _text_connection(alice, model="m2")
        over = await alice.post(
            CONN,
            json={
                "label": "third",
                "base_url": TEXT_URL,
                "models": [{"model_id": "m3", "protocol": "openai_chat"}],
                "api_key": ENDPOINT_KEY,
            },
        )
        assert over.status_code == 422
        assert (await alice.get(CONN)).json()["limit"] == 2
        # 软删的不占名额
        assert (await alice.delete(f"{CONN}/{first['id']}")).status_code == 204
        await _text_connection(alice, model="m3")
    finally:
        if previous is None:
            os.environ.pop("ORG_PROVIDER_CONNECTION_LIMIT", None)
        else:
            os.environ["ORG_PROVIDER_CONNECTION_LIMIT"] = previous
        get_settings.cache_clear()


# ------------------------------------------------------------------ 连接：CRUD 与掩码


async def test_connection_crud_and_key_masking(alice: AsyncClient) -> None:
    # 从预设建：只给 Key 和地域地址，其余从模板拷
    created = await alice.post(CONN, json={"preset_id": "zhipu_cogview", "api_key": IMAGE_KEY})
    assert created.status_code == 201
    assert IMAGE_KEY not in created.text
    image = created.json()
    assert image["preset_id"] == "zhipu_cogview" and image["label"] == "智谱 CogView"
    assert image["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert [m["model_id"] for m in image["models"]] == [
        "cogview-4-250304",
        "cogview-4",
        "cogview-3-flash",
    ]
    assert {(m["protocol"], m["capability"]) for m in image["models"]} == {("openai_images", IMAGE)}
    assert all(m["consistency_verified"] is False for m in image["models"])
    assert image["provider_id"] == f"{ORG}{image['id']}"
    assert image["masked_key"].endswith(IMAGE_KEY[-4:]) and "•" in image["masked_key"]
    assert image["enabled"] is True

    # 自定义建
    text_conn = await _text_connection(alice)
    assert text_conn["preset_id"] is None and text_conn["base_url"] == TEXT_URL
    assert [m["consistency_verified"] for m in text_conn["models"]] == [None]

    listing = await alice.get(CONN)
    assert listing.status_code == 200
    assert {i["id"] for i in listing.json()["items"]} == {image["id"], text_conn["id"]}
    assert listing.json()["limit"] == get_settings().org_provider_connection_limit
    assert IMAGE_KEY not in listing.text and ENDPOINT_KEY not in listing.text

    detail = await alice.get(f"{CONN}/{image['id']}")
    assert detail.status_code == 200 and detail.json() == image

    # 改名 + 换 Key：响应只有新尾号
    patched = await alice.patch(
        f"{CONN}/{text_conn['id']}", json={"label": "新网关", "api_key": NEW_KEY}
    )
    assert patched.status_code == 200
    assert NEW_KEY not in patched.text and ENDPOINT_KEY not in patched.text
    assert patched.json()["label"] == "新网关"
    assert patched.json()["masked_key"].endswith(NEW_KEY[-4:])
    # 不给 Key 就沿用原来那把
    kept = await alice.patch(f"{CONN}/{text_conn['id']}", json={"enabled": False})
    assert kept.json()["masked_key"] == patched.json()["masked_key"]
    assert kept.json()["enabled"] is False

    # 聚合视图：图像能力下多出这个连接，带"未实测"；停用的文本连接列出但不可用
    body = (await alice.get(BASE)).json()
    image_opts = {p["provider_id"]: p for p in _item(body, IMAGE)["providers"]}
    org_image = image_opts[image["provider_id"]]
    assert org_image["kind"] == "org" and org_image["connection_id"] == image["id"]
    assert org_image["available"] is True and org_image["supports_platform_key"] is False
    assert org_image["consistency_verified"] is False
    assert image_opts["provider.dashscope"]["consistency_verified"] is None
    text_opts = {p["provider_id"]: p for p in _item(body, TEXT)["providers"]}
    org_text = text_opts[text_conn["provider_id"]]
    assert org_text["available"] is False and org_text["unavailable_reason"]
    assert org_text["consistency_verified"] is None
    # 出图连接不会出现在文本能力下，反之亦然
    assert image["provider_id"] not in text_opts
    assert text_conn["provider_id"] not in image_opts
    assert IMAGE_KEY not in str(body) and NEW_KEY not in str(body)

    # 测试连接：ENV=test 走 Mock 探测，不打用户填的地址
    saved = await alice.post(f"{CONN}/{image['id']}/test", json={})
    assert saved.status_code == 200 and saved.json()["ok"] is True
    assert saved.json()["provider_id"] == image["provider_id"]
    assert IMAGE_KEY not in saved.text
    draft = await alice.post(
        f"{CONN}/test",
        json={
            "protocol": "openai_chat",
            "model_id": "m",
            "base_url": TEXT_URL,
            "api_key": ENDPOINT_KEY,
        },
    )
    assert draft.status_code == 200 and draft.json()["ok"] is True
    assert ENDPOINT_KEY not in draft.text
    # 地址不合法：照样给"测试结论"而不是 4xx（与 probe.verify 一致）
    bad = await alice.post(
        f"{CONN}/test",
        json={
            "protocol": "openai_chat",
            "model_id": "m",
            "base_url": "https://169.254.169.254/v1",
            "api_key": ENDPOINT_KEY,
        },
    )
    assert bad.status_code == 200 and bad.json()["ok"] is False

    # 软删
    assert (await alice.delete(f"{CONN}/{image['id']}")).status_code == 204
    assert (await alice.get(f"{CONN}/{image['id']}")).status_code == 404
    assert (await alice.delete(f"{CONN}/{image['id']}")).status_code == 404
    assert [i["id"] for i in (await alice.get(CONN)).json()["items"]] == [text_conn["id"]]


async def test_other_tenant_connection_is_404(alice: AsyncClient, bob: AsyncClient) -> None:
    conn = await _text_connection(alice)
    cid = conn["id"]

    assert (await bob.get(CONN)).json()["items"] == []
    assert (await bob.get(f"{CONN}/{cid}")).status_code == 404
    assert (await bob.patch(f"{CONN}/{cid}", json={"label": "x"})).status_code == 404
    assert (await bob.post(f"{CONN}/{cid}/test", json={})).status_code == 404
    assert (await bob.delete(f"{CONN}/{cid}")).status_code == 404
    # 不能把别人的连接设成自己的默认，也不能写进自己项目的偏好
    put = await bob.put(
        f"{BASE}/{TEXT}", json={"provider_id": f"{ORG}{cid}", "model_id": None, "key_source": "org"}
    )
    assert put.status_code == 404
    pid = (await bob.post("/api/v1/projects", json={"title": "p"})).json()["id"]
    pref = await bob.patch(
        f"/api/v1/projects/{pid}/model-preference",
        json={"capability": TEXT, "model_id": f"{ORG}{cid}"},
    )
    assert pref.status_code == 404
    # 不存在的 id 也是 404，两者不可区分
    assert (await bob.get(f"{CONN}/{uuid.uuid4()}")).status_code == 404

    # alice 的连接原封不动
    again = (await alice.get(f"{CONN}/{cid}")).json()
    assert again["label"] == conn["label"] and again["enabled"] is True


# ------------------------------------------------------------------ 默认指向连接 → 解析与计费


async def test_text_default_on_connection_resolves_and_bills_as_own_key(
    alice: AsyncClient,
) -> None:
    conn = await _text_connection(alice)
    pid_ref = conn["provider_id"]

    # 组织连接只能自有计费；模型必须在连接里
    bad = await alice.put(
        f"{BASE}/{TEXT}", json={"provider_id": pid_ref, "model_id": None, "key_source": "platform"}
    )
    assert bad.status_code == 422
    missing = await alice.put(
        f"{BASE}/{TEXT}", json={"provider_id": pid_ref, "model_id": "gpt-x", "key_source": "org"}
    )
    assert missing.status_code == 400
    # 文本连接不能当出图默认
    wrong_cap = await alice.put(
        f"{BASE}/{IMAGE}", json={"provider_id": pid_ref, "model_id": None, "key_source": "org"}
    )
    assert wrong_cap.status_code == 400

    ok = await alice.put(
        f"{BASE}/{TEXT}",
        json={"provider_id": pid_ref, "model_id": "qwen2.5-72b-instruct", "key_source": "org"},
    )
    assert ok.status_code == 200 and ENDPOINT_KEY not in ok.text
    sel = _item(ok.json(), TEXT)["selection"]
    assert (sel["provider_id"], sel["model_id"], sel["key_source"], sel["layer"]) == (
        pid_ref,
        "qwen2.5-72b-instruct",
        "org",
        "org",
    )
    assert sel["broken_reason"] is None

    org = await _org(alice)
    resolution = await gw._resolve(TEXT, org_id=org)
    # 只有这一条路由：失败不回落平台、不换连接
    (route,) = resolution.routes
    adapter = route.factory()
    assert isinstance(adapter, OpenAICompatTextProvider)
    assert (route.provider_id, adapter.model_id, adapter._base_url, adapter._api_key) == (
        pid_ref,
        "qwen2.5-72b-instruct",
        TEXT_URL,
        ENDPOINT_KEY,
    )
    assert route.key_source is KeySource.ORG and resolution.key_source is KeySource.ORG
    async with session_scope() as db:
        # 计费与调用同一个结论
        assert await pricing.uses_own_key(db, org_id=org, capability=TEXT) is True


async def test_image_task_records_the_connection_it_will_call(alice: AsyncClient) -> None:
    conn = await _image_connection(alice)
    ok = await alice.put(
        f"{BASE}/{IMAGE}",
        json={"provider_id": conn["provider_id"], "model_id": "cogview-4", "key_source": "org"},
    )
    assert ok.status_code == 200

    org = await _org(alice)
    async with session_scope() as db:
        assert await pricing.uses_own_key(db, org_id=org, capability=IMAGE) is True

    created = await alice.post(
        "/api/v1/tasks", json={"type": "image.generate", "input": {"prompt": "一只猫", "n": 1}}
    )
    assert created.status_code == 201, created.text
    async with session_scope() as db:
        input_json = (
            await db.execute(
                text("SELECT input_json FROM tasks WHERE id = :t"),
                {"t": uuid.UUID(created.json()["id"])},
            )
        ).scalar_one()
    assert input_json["upstream"] == {
        "capability": IMAGE,
        "provider_id": conn["provider_id"],
        "connection_id": conn["id"],
        "model_id": "cogview-4",
        "layer": "org",
        "key_source": "org",
    }
    assert IMAGE_KEY not in str(input_json)

    # 出图解析（放开 ENV=test 的强制 Mock 之后）同样只有这个连接一条路由
    resolution = await _resolve_without_mock(IMAGE, org)
    (route,) = resolution.routes
    adapter = route.factory()
    assert isinstance(adapter, OpenAIImagesProvider)
    assert (route.provider_id, adapter.model_id, adapter._base_url, adapter._api_key) == (
        conn["provider_id"],
        "cogview-4",
        IMAGE_URL,
        IMAGE_KEY,
    )


async def _resolve_without_mock(capability: str, org: uuid.UUID) -> gw.Resolution:
    """`ENV=test` 时出图在 `_resolve` 最前面强制 Mock；只看解析结论时临时放开那道闸。"""
    forced = gw.mock_image.forced
    gw.mock_image.forced = lambda c: False  # type: ignore[assignment]
    try:
        return await gw._resolve(capability, org_id=org)
    finally:
        gw.mock_image.forced = forced  # type: ignore[assignment]


@pytest.mark.parametrize("action", ["disable", "delete", "drop_model"])
async def test_broken_connection_is_a_readable_error_not_platform(
    alice: AsyncClient, action: str
) -> None:
    conn = await _text_connection(alice)
    cid, ref = conn["id"], conn["provider_id"]
    # 就算这个 org 还存着 DeepSeek 的 Key，也不拿它顶上
    assert (
        await alice.put(
            f"/api/v1/provider-credentials/{TEXT}?provider_id=provider.deepseek",
            json={"api_key": DEEPSEEK_KEY},
        )
    ).status_code == 200
    assert (
        await alice.put(
            f"{BASE}/{TEXT}",
            json={"provider_id": ref, "model_id": "qwen2.5-72b-instruct", "key_source": "org"},
        )
    ).status_code == 200

    if action == "disable":
        resp = await alice.patch(f"{CONN}/{cid}", json={"enabled": False})
        reason = "connection_disabled"
    elif action == "delete":
        resp = await alice.delete(f"{CONN}/{cid}")
        reason = "connection_missing"
    else:
        resp = await alice.patch(
            f"{CONN}/{cid}", json={"models": [{"model_id": "other", "protocol": "openai_chat"}]}
        )
        reason = "model_missing"
    assert resp.status_code in (200, 204)

    # 默认不被自动改掉，界面拿到原因码
    sel = _item((await alice.get(BASE)).json(), TEXT)["selection"]
    assert (sel["provider_id"], sel["layer"], sel["broken_reason"]) == (ref, "org", reason)

    org = await _org(alice)
    with pytest.raises(AppError) as exc:
        await gw._resolve(TEXT, org_id=org)
    assert exc.value.code == "provider.byok.rejected"
    assert exc.value.detail["reason"] == reason
    # 计费也仍按自有 Key 判（与调用同一份结论），不悄悄换成平台档
    async with session_scope() as db:
        assert await pricing.uses_own_key(db, org_id=org, capability=TEXT) is True


async def test_image_task_on_broken_connection_is_refused_before_billing(
    alice: AsyncClient,
) -> None:
    conn = await _image_connection(alice)
    assert (
        await alice.put(
            f"{BASE}/{IMAGE}",
            json={"provider_id": conn["provider_id"], "model_id": None, "key_source": "org"},
        )
    ).status_code == 200
    assert (await alice.delete(f"{CONN}/{conn['id']}")).status_code == 204

    before = (await alice.get("/api/v1/credits/balance")).json()
    resp = await alice.post(
        "/api/v1/tasks", json={"type": "image.generate", "input": {"prompt": "一只猫", "n": 1}}
    )
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "provider.byok.rejected"
    assert (await alice.get("/api/v1/credits/balance")).json() == before
    org = await _org(alice)
    async with session_scope() as db:
        n = (
            await db.execute(text("SELECT count(*) FROM tasks WHERE org_id = :o"), {"o": org})
        ).scalar_one()
    assert n == 0


# ------------------------------------------------------------------ 项目偏好指向连接


async def test_project_preference_on_connection_is_billed_as_own_key(alice: AsyncClient) -> None:
    conn = await _text_connection(alice, model="m1")
    image = await _image_connection(alice)
    pid = (await alice.post("/api/v1/projects", json={"title": "p"})).json()["id"]

    value = f"{conn['provider_id']}:m1"
    resp = await alice.patch(
        f"/api/v1/projects/{pid}/model-preference", json={"capability": TEXT, "model_id": value}
    )
    assert resp.status_code == 200 and resp.json()["model_preference"][TEXT] == value
    org = await _org(alice)
    async with session_scope() as db:
        # 组织层是平台默认（没选过），项目层把文本改到了连接——按自有计费
        assert await pricing.uses_own_key(db, org_id=org, capability=TEXT) is False
        assert (
            await pricing.uses_own_key(db, org_id=org, capability=TEXT, project_id=uuid.UUID(pid))
            is True
        )
    resolution = await gw._resolve(TEXT, org_id=org, preferred_model_id=value)
    (route,) = resolution.routes
    assert (route.provider_id, route.model_id) == (conn["provider_id"], "m1")

    # 能力对不上 / 模型不在连接里 / 格式不对：400
    for capability, ref in (
        (IMAGE, conn["provider_id"]),
        (TEXT, image["provider_id"]),
        (TEXT, f"{conn['provider_id']}:not-there"),
        (TEXT, f"{ORG}not-a-uuid"),
        (TEXT, LEGACY),
    ):
        bad = await alice.patch(
            f"/api/v1/projects/{pid}/model-preference",
            json={"capability": capability, "model_id": ref},
        )
        assert bad.status_code == 400, (capability, ref)
    # 出图能力可以指向出图连接
    ok = await alice.patch(
        f"/api/v1/projects/{pid}/model-preference",
        json={"capability": IMAGE, "model_id": f"{image['provider_id']}:cogview-4"},
    )
    assert ok.status_code == 200


# ------------------------------------------------------------------ Key 不进日志


async def test_key_never_reaches_the_logs(
    alice: AsyncClient, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.DEBUG)
    conn = await _text_connection(alice)
    await alice.patch(f"{CONN}/{conn['id']}", json={"api_key": NEW_KEY})
    await alice.post(f"{CONN}/{conn['id']}/test", json={})
    await alice.post(
        f"{CONN}/test",
        json={
            "protocol": "openai_images",
            "model_id": "m",
            "base_url": IMAGE_URL,
            "api_key": IMAGE_KEY,
        },
    )
    await alice.put(
        f"{BASE}/{TEXT}",
        json={"provider_id": conn["provider_id"], "model_id": None, "key_source": "org"},
    )
    await alice.delete(f"{CONN}/{conn['id']}")

    out = capsys.readouterr()
    logs = caplog.text + out.out + out.err
    # 确实抓到了日志，不是空集上的断言
    assert "upstreams.connection_created" in logs and "upstreams.connection_updated" in logs
    for secret in (ENDPOINT_KEY, NEW_KEY, IMAGE_KEY):
        assert secret not in logs
        assert secret[-12:] not in logs


# ------------------------------------------------------------------ 迁移 round-trip


def _alembic(fn: str, rev: str) -> None:
    getattr(command, fn)(Config("alembic.ini"), rev)


async def _migrate(fn: str, rev: str) -> None:
    """跑迁移并丢掉连接池：池里的连接缓存着旧表结构的语句计划，列类型一改就失效。"""
    await asyncio.to_thread(_alembic, fn, rev)
    await dispose_engine()


async def test_migration_rewrites_legacy_endpoint_and_round_trips(alice: AsyncClient) -> None:
    """旧文本自定义端点 → 同 id 的连接，默认与项目偏好改写；降级逐字还原。"""
    org = await _org(alice)
    user = uuid.UUID((await alice.get("/api/v1/auth/me")).json()["id"])
    pid = uuid.UUID((await alice.post("/api/v1/projects", json={"title": "p"})).json()["id"])
    live, gone = uuid.uuid4(), uuid.uuid4()
    key_ct = encrypt_secret(ENDPOINT_KEY)
    endpoint_sql = (
        "SELECT id, org_id, label, base_url, model_id, key_encrypted, created_by, "
        "created_at, updated_at, deleted_at FROM org_text_endpoints WHERE org_id = :o ORDER BY id"
    )

    await _migrate("downgrade", PRE_CONNECTIONS)
    try:
        async with session_scope() as db:
            await db.execute(
                text(
                    "INSERT INTO org_text_endpoints "
                    "(id, org_id, label, base_url, model_id, key_encrypted, created_by, "
                    "deleted_at) "
                    "VALUES (:id, :o, '公司网关', :url, 'qwen2.5-72b-instruct', :k, :u, NULL), "
                    "(:gone, :o, '旧网关', :url, 'old-model', :k, :u, now())"
                ),
                {"id": live, "gone": gone, "o": org, "url": TEXT_URL, "k": key_ct, "u": user},
            )
            await db.execute(
                text(
                    "INSERT INTO org_model_defaults "
                    "(org_id, capability, provider_id, model_id, key_source, updated_by) "
                    "VALUES (:o, 'text_generation', :p, NULL, 'org', :u)"
                ),
                {"o": org, "p": LEGACY, "u": user},
            )
            await db.execute(
                text("UPDATE projects SET model_preference = CAST(:pref AS jsonb) WHERE id = :pid"),
                {
                    "pid": pid,
                    "pref": f'{{"{TEXT}": "{LEGACY}", "{IMAGE}": "wan2.2-t2i-plus"}}',
                },
            )
            await db.commit()
            before = [tuple(r) for r in (await db.execute(text(endpoint_sql), {"o": org})).all()]
    finally:
        await _migrate("upgrade", "head")

    ref = f"{ORG}{live}"
    async with session_scope() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT id, label, preset_id, base_url, models, enabled, "
                    "deleted_at IS NOT NULL "
                    "FROM org_provider_connections WHERE org_id = :o ORDER BY id"
                ),
                {"o": org},
            )
        ).all()
        by_id = {r[0]: tuple(r[1:]) for r in rows}
        assert by_id[live] == (
            "公司网关",
            None,
            TEXT_URL,
            [{"model_id": "qwen2.5-72b-instruct", "protocol": "openai_chat"}],
            True,
            False,
        )
        assert by_id[gone][3:] == (
            [{"model_id": "old-model", "protocol": "openai_chat"}],
            True,
            True,
        )
        default = (
            await db.execute(
                text(
                    "SELECT provider_id, model_id, key_source FROM org_model_defaults "
                    "WHERE org_id = :o AND deleted_at IS NULL"
                ),
                {"o": org},
            )
        ).one()
        assert tuple(default) == (ref, None, "org")
        pref = (
            await db.execute(
                text("SELECT model_preference FROM projects WHERE id = :p"), {"p": pid}
            )
        ).scalar_one()
        assert pref == {TEXT: ref, IMAGE: "wan2.2-t2i-plus"}
        assert await pricing.uses_own_key(db, org_id=org, capability=TEXT) is True

    # 迁过来的行在新代码下照常解析：同一个地址、同一把 Key
    (route,) = (await gw._resolve(TEXT, org_id=org)).routes
    adapter = route.factory()
    assert (route.provider_id, adapter.model_id, adapter._base_url, adapter._api_key) == (
        ref,
        "qwen2.5-72b-instruct",
        TEXT_URL,
        ENDPOINT_KEY,
    )

    # 降级：逐字还原（同 id、同字段、同时间戳），引用改回旧固定串
    await _migrate("downgrade", PRE_CONNECTIONS)
    try:
        async with session_scope() as db:
            after = [tuple(r) for r in (await db.execute(text(endpoint_sql), {"o": org})).all()]
            assert after == before
            assert decrypt_secret(after[0][5]) == ENDPOINT_KEY
            default = (
                await db.execute(
                    text(
                        "SELECT provider_id, model_id, key_source FROM org_model_defaults "
                        "WHERE org_id = :o AND deleted_at IS NULL"
                    ),
                    {"o": org},
                )
            ).one()
            assert tuple(default) == (LEGACY, None, "org")
            pref = (
                await db.execute(
                    text("SELECT model_preference FROM projects WHERE id = :p"), {"p": pid}
                )
            ).scalar_one()
            assert pref == {TEXT: LEGACY, IMAGE: "wan2.2-t2i-plus"}
    finally:
        await _migrate("upgrade", "head")

    async with session_scope() as db:
        await db.execute(text("DELETE FROM org_model_defaults WHERE org_id = :o"), {"o": org})
        await db.execute(text("DELETE FROM org_provider_connections WHERE org_id = :o"), {"o": org})
        await db.commit()


async def test_migration_backfills_legacy_byok_and_round_trips() -> None:
    """旧 BYOK 行 → 升级后补一条显式的组织默认（key_source=org），降级再升级仍成立。

    降到 `a8c3e91d6402` 会经过连接表迁移的降级，这条一并证明它在空数据上可逆。
    """
    org, user = uuid.uuid4(), uuid.uuid4()
    await asyncio.to_thread(_alembic, "downgrade", "a8c3e91d6402")
    try:
        async with session_scope() as db:
            await db.execute(
                text(
                    "INSERT INTO provider_credentials "
                    "(org_id, capability, provider_id, key_encrypted, created_by) "
                    "VALUES (:o, 'text_generation', 'provider.deepseek', :k, :u)"
                ),
                {"o": org, "k": encrypt_secret(DEEPSEEK_KEY), "u": user},
            )
            await db.commit()
    finally:
        await asyncio.to_thread(_alembic, "upgrade", "head")

    async with session_scope() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT provider_id, model_id, key_source, updated_by FROM org_model_defaults "
                    "WHERE org_id = :o AND deleted_at IS NULL"
                ),
                {"o": org},
            )
        ).all()
        assert [tuple(r) for r in rows] == [("provider.deepseek", None, "org", user)]
        # 旧数据在新代码下照常解析为自有 Key
        assert await pricing.uses_own_key(db, org_id=org, capability=TEXT) is True

    # 再走一遍降级→升级：幂等、不重复补行
    await asyncio.to_thread(_alembic, "downgrade", "a8c3e91d6402")
    await asyncio.to_thread(_alembic, "upgrade", "head")
    async with session_scope() as db:
        n = (
            await db.execute(
                text(
                    "SELECT count(*) FROM org_model_defaults "
                    "WHERE org_id = :o AND deleted_at IS NULL"
                ),
                {"o": org},
            )
        ).scalar_one()
        assert n == 1
        await db.execute(text("DELETE FROM org_model_defaults WHERE org_id = :o"), {"o": org})
        await db.execute(text("DELETE FROM provider_credentials WHERE org_id = :o"), {"o": org})
        await db.commit()
