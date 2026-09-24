"""模型上游配置的整条链路（CONFIG_STAGE_UPLOAD_0924 · A）。

HTTP 存配置 → 落库 → Gateway 解析 → 计费判断。

单测（`tests/unit/test_upstream_selection.py`）把查库那三处换成了假件；
这里补的正是被换掉的那一段：**真的走 HTTP 存、真的落 `org_model_defaults` /
`org_text_endpoints`、真的被 `_resolve` 读出来、计费用的是同一个结论**。

不打任何上游：自定义端点只构造适配器、不发请求；出网地址只做形状校验。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from alembic import command
from alembic.config import Config
from httpx import AsyncClient
from sqlalchemy import text

from adapters.providers.base import KeySource
from apps.api.core.crypto import encrypt_secret
from apps.api.core.db import session_scope
from apps.api.modules.billing import pricing
from apps.api.modules.gateway import catalog
from apps.api.modules.gateway import service as gw

pytestmark = pytest.mark.integration

BASE = "/api/v1/model-config"
TEXT = "text_generation"
IMAGE = "image_generation"
CUSTOM = catalog.CUSTOM_TEXT_PROVIDER_ID
ENDPOINT_KEY = "sk-custom-endpoint-secret-0123456789"
DEEPSEEK_KEY = "sk-deepseek-org-key-abcdef0123456"


async def _org(client: AsyncClient) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/v1/auth/me")).json()["org_id"])


def _item(body: dict, capability: str) -> dict:
    return next(i for i in body["items"] if i["capability"] == capability)


async def test_catalog_shape_and_platform_default(alice: AsyncClient) -> None:
    body = (await alice.get(BASE)).json()
    text_item, image_item = _item(body, TEXT), _item(body, IMAGE)

    providers = {p["provider_id"]: p for p in text_item["providers"]}
    assert providers["provider.deepseek"]["available"] is True
    assert [m["model_id"] for m in providers["provider.deepseek"]["models"]] == list(
        catalog.model_ids(TEXT, "provider.deepseek")
    )
    # 没填端点时自定义端点是一个"不可用"的选项，不是一个能选的假下拉
    assert providers[CUSTOM]["available"] is False and providers[CUSTOM]["models"] == []
    assert text_item["selection"]["layer"] == "platform"

    image_providers = [p["provider_id"] for p in image_item["providers"]]
    assert image_providers == ["provider.dashscope"]
    assert image_item["supports_custom_endpoint"] is False

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


async def test_private_endpoint_url_is_rejected(alice: AsyncClient) -> None:
    for url in (
        "https://169.254.169.254/v1",
        "http://api.example.com/v1",
        "https://u:p@example.com/v1",
    ):
        resp = await alice.put(
            f"{BASE}/{TEXT}/custom-endpoint",
            json={"label": "x", "base_url": url, "model_id": "m", "api_key": ENDPOINT_KEY},
        )
        assert resp.status_code == 422, url
    assert _item((await alice.get(BASE)).json(), TEXT)["custom_endpoint"] is None


async def test_custom_endpoint_end_to_end(alice: AsyncClient, bob: AsyncClient) -> None:
    resp = await alice.put(
        f"{BASE}/{TEXT}/custom-endpoint",
        json={
            "label": "公司网关",
            "base_url": "https://llm.example.com/v1/",
            "model_id": "qwen2.5-72b-instruct",
            "api_key": ENDPOINT_KEY,
        },
    )
    assert resp.status_code == 200
    assert ENDPOINT_KEY not in resp.text  # 明文永远不出服务端
    ep = _item(resp.json(), TEXT)["custom_endpoint"]
    assert ep["base_url"] == "https://llm.example.com/v1"
    assert ep["masked_key"].endswith(ENDPOINT_KEY[-4:]) and "•" in ep["masked_key"]

    # 自定义端点只能自有计费
    bad = await alice.put(
        f"{BASE}/{TEXT}", json={"provider_id": CUSTOM, "model_id": None, "key_source": "platform"}
    )
    assert bad.status_code == 422
    ok = await alice.put(
        f"{BASE}/{TEXT}", json={"provider_id": CUSTOM, "model_id": None, "key_source": "org"}
    )
    assert ok.status_code == 200 and ENDPOINT_KEY not in ok.text

    body = (await alice.get(BASE)).json()
    assert _item(body, TEXT)["selection"]["provider_id"] == CUSTOM
    assert ENDPOINT_KEY not in str(body)

    org = await _org(alice)
    resolution = await gw._resolve(TEXT, org_id=org)
    (route,) = resolution.routes
    adapter = route.factory()
    assert (route.provider_id, adapter.model_id, adapter._base_url, adapter._api_key) == (
        CUSTOM,
        "qwen2.5-72b-instruct",
        "https://llm.example.com/v1",
        ENDPOINT_KEY,
    )
    assert resolution.key_source is KeySource.ORG
    async with session_scope() as db:
        assert await pricing.uses_own_key(db, org_id=org, capability=TEXT) is True

    # 测试连接：ENV=test 走 Mock 探测，不打用户填的地址
    probe = await alice.post(f"{BASE}/{TEXT}/custom-endpoint/test", json={})
    assert probe.status_code == 200 and probe.json()["ok"] is True
    assert ENDPOINT_KEY not in probe.text

    # 别的租户看不到
    assert _item((await bob.get(BASE)).json(), TEXT)["custom_endpoint"] is None

    # 删除端点：组织默认一并退回平台目录默认
    assert (await alice.delete(f"{BASE}/{TEXT}/custom-endpoint")).status_code == 204
    sel = _item((await alice.get(BASE)).json(), TEXT)["selection"]
    assert sel["layer"] == "platform" and sel["provider_id"] == "provider.deepseek"


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


async def test_project_custom_override_is_accepted_and_billed_as_own_key(
    alice: AsyncClient,
) -> None:
    await alice.put(
        f"{BASE}/{TEXT}/custom-endpoint",
        json={
            "label": "g",
            "base_url": "https://llm.example.com/v1",
            "model_id": "m1",
            "api_key": ENDPOINT_KEY,
        },
    )
    pid = (await alice.post("/api/v1/projects", json={"title": "p"})).json()["id"]
    resp = await alice.patch(
        f"/api/v1/projects/{pid}/model-preference", json={"capability": TEXT, "model_id": CUSTOM}
    )
    assert resp.status_code == 200 and resp.json()["model_preference"][TEXT] == CUSTOM
    org = await _org(alice)
    async with session_scope() as db:
        # 组织层是平台默认（没选过），项目层把文本改到了自定义端点——按自有计费
        assert await pricing.uses_own_key(db, org_id=org, capability=TEXT) is False
        assert (
            await pricing.uses_own_key(db, org_id=org, capability=TEXT, project_id=uuid.UUID(pid))
            is True
        )
    # 图片能力不接受这个值
    bad = await alice.patch(
        f"/api/v1/projects/{pid}/model-preference", json={"capability": IMAGE, "model_id": CUSTOM}
    )
    assert bad.status_code == 400


# ------------------------------------------------------------------ 迁移 round-trip


def _alembic(fn: str, rev: str) -> None:
    getattr(command, fn)(Config("alembic.ini"), rev)


async def test_migration_backfills_legacy_byok_and_round_trips() -> None:
    """旧 BYOK 行 → 升级后补一条显式的组织默认（key_source=org），降级再升级仍成立。"""
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
