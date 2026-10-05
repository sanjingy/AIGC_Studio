"""多供应商 A3（Lead 对 A1 报告 §7 的三项决定）：

- **(a) 出图任务钉住上游**：Worker 执行时按 `input_json.upstream` 记下的组织连接 / 模型解析，
  建任务之后改默认不影响这个任务；钉住的连接被删 / 停用 / 模型被移除 → 可读错误，
  不落平台、不换连接，预扣按错误目录释放；没有 `upstream` 的旧任务、平台路由照旧。
- **(b) 推理模型标记**：连接模型的 `reasoning`（缺省 false）往返；`no_reasoning_roles`
  的调用（`allow_reasoning=False`）解析到标了推理的连接模型 → 拒绝，其他调用放行。
- **(d) 连接引用查询**：`GET /model-config/connections/{id}/references`。

不打任何上游：入队掐掉、由用例同步执行；`ENV=test` 下出图强制 Mock，只在看解析结论时临时放开；
连接地址一律保留域名 `.invalid`。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text

from adapters.providers.base import KeySource, TextRequest
from apps.api.core.db import session_scope
from apps.api.core.errors import AppError
from apps.api.modules.gateway import service as gw
from apps.api.modules.task import service as task_service
from apps.api.modules.task.models import Task
from worker.jobs.execute import execute_task

pytestmark = pytest.mark.integration

BASE = "/api/v1/model-config"
CONN = f"{BASE}/connections"
TEXT = "text_generation"
IMAGE = "image_generation"
KEY_A = "sk-pinned-connection-key-aaaaaaaa01"
KEY_B = "sk-other-connection-key-bbbbbbbbb02"
TEXT_KEY = "sk-text-connection-key-cccccccccc03"


@pytest.fixture(autouse=True)
def _no_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    """掐掉入队：跑在 `ENV=local` 的 worker 容器不认测试的 Mock 开关，不能让它捞到任务。"""

    async def _noop(task_id: uuid.UUID) -> None:
        del task_id

    monkeypatch.setattr(task_service, "_enqueue", _noop)


@pytest.fixture
def real_resolution() -> Iterator[None]:
    """临时放开 `ENV=test` 的出图强制 Mock，只看解析结论；不调用任何适配器。"""
    forced = gw.mock_image.forced
    gw.mock_image.forced = lambda capability: False  # type: ignore[assignment]
    yield
    gw.mock_image.forced = forced  # type: ignore[assignment]


async def _org(client: AsyncClient) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/v1/auth/me")).json()["org_id"])


async def _image_conn(client: AsyncClient, *, label: str, host: str, key: str) -> dict:
    resp = await client.post(
        CONN,
        json={
            "label": label,
            "base_url": f"https://{host}.example.invalid/v1",
            "models": [
                {"model_id": "img-a", "protocol": "openai_images"},
                {"model_id": "img-b", "protocol": "openai_images"},
            ],
            "api_key": key,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _set_default(client: AsyncClient, capability: str, conn: dict, model: str | None) -> None:
    resp = await client.put(
        f"{BASE}/{capability}",
        json={"provider_id": conn["provider_id"], "model_id": model, "key_source": "org"},
    )
    assert resp.status_code == 200, resp.text


async def _create_image_task(client: AsyncClient) -> str:
    resp = await client.post(
        "/api/v1/tasks", json={"type": "image.generate", "input": {"prompt": "一只猫", "n": 1}}
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _task(task_id: str) -> Task:
    async with session_scope() as db:
        return (await db.execute(select(Task).where(Task.id == uuid.UUID(task_id)))).scalar_one()


async def _balance(client: AsyncClient) -> Any:
    return (await client.get("/api/v1/credits/balance")).json()


# ------------------------------------------------------------------ (a) 钉住上游


async def test_task_keeps_its_pinned_connection_after_default_changes(
    alice: AsyncClient, real_resolution: None
) -> None:
    org = await _org(alice)
    a = await _image_conn(alice, label="A", host="a", key=KEY_A)
    b = await _image_conn(alice, label="B", host="b", key=KEY_B)
    await _set_default(alice, IMAGE, a, "img-b")
    task_id = await _create_image_task(alice)

    # 建任务之后把默认改到 B
    await _set_default(alice, IMAGE, b, "img-a")

    upstream = dict((await _task(task_id)).input_json or {})["upstream"]
    assert (upstream["provider_id"], upstream["model_id"]) == (a["provider_id"], "img-b")

    (route,) = (await gw._resolve(IMAGE, org_id=org, upstream=upstream)).routes
    adapter = route.factory()
    assert (route.provider_id, route.model_id, adapter._api_key) == (
        a["provider_id"],
        "img-b",
        KEY_A,
    )
    assert route.key_source is KeySource.ORG
    # 对照：不带钉住的上游时解析到的是新默认
    (now,) = (await gw._resolve(IMAGE, org_id=org)).routes
    assert (now.provider_id, now.model_id) == (b["provider_id"], "img-a")


async def test_worker_passes_the_pinned_upstream_to_the_gateway(
    alice: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Worker 真的把 `input_json.upstream` 交给 Gateway（不只是 Gateway 能接）。"""
    a = await _image_conn(alice, label="A", host="a", key=KEY_A)
    await _set_default(alice, IMAGE, a, None)
    task_id = await _create_image_task(alice)
    stored = dict((await _task(task_id)).input_json or {})["upstream"]

    seen: dict[str, Any] = {}
    real = gw.generate_image

    async def _spy(request: Any, **kw: Any) -> Any:
        seen.update(kw)
        return await real(request, **kw)

    monkeypatch.setattr(gw, "generate_image", _spy)
    assert await execute_task({}, task_id) == "succeeded"  # ENV=test：校验过连接后走 Mock
    assert seen["upstream"] == stored


@pytest.mark.parametrize(
    ("action", "reason"),
    [
        ("delete", "connection_missing"),
        ("disable", "connection_disabled"),
        ("drop_model", "model_missing"),
    ],
)
async def test_broken_pinned_connection_fails_without_fallback(
    alice: AsyncClient, action: str, reason: str
) -> None:
    org = await _org(alice)
    a = await _image_conn(alice, label="A", host="a", key=KEY_A)
    await _set_default(alice, IMAGE, a, "img-b")
    before = await _balance(alice)
    task_id = await _create_image_task(alice)
    assert (await _balance(alice)) != before, "建任务应当预扣"

    if action == "delete":
        resp = await alice.delete(f"{CONN}/{a['id']}")
    elif action == "disable":
        resp = await alice.patch(f"{CONN}/{a['id']}", json={"enabled": False})
    else:
        resp = await alice.patch(
            f"{CONN}/{a['id']}",
            json={"models": [{"model_id": "img-a", "protocol": "openai_images"}]},
        )
    assert resp.status_code in (200, 204)

    upstream = dict((await _task(task_id)).input_json or {})["upstream"]
    with pytest.raises(AppError) as exc:
        await gw._resolve(IMAGE, org_id=org, upstream=upstream)
    assert exc.value.code == "provider.byok.rejected"
    assert exc.value.detail["reason"] == reason

    # 整条 Worker 路径：失败、没有产出、预扣按错误目录（RELEASE）释放
    assert await execute_task({}, task_id) == "failed"
    row = await _task(task_id)
    assert row.status == "failed" and row.error_code == "provider.byok.rejected"
    assert row.output_json is None and row.actual_cost == 0
    assert await _balance(alice) == before


async def test_task_without_upstream_behaves_as_before(alice: AsyncClient) -> None:
    """A3 之前建的任务没有 `upstream`：照旧按默认解析（`ENV=test` 下是 Mock）。"""
    a = await _image_conn(alice, label="A", host="a", key=KEY_A)
    await _set_default(alice, IMAGE, a, None)
    task_id = await _create_image_task(alice)
    async with session_scope() as db:
        await db.execute(
            text("UPDATE tasks SET input_json = input_json - 'upstream' WHERE id = :t"),
            {"t": uuid.UUID(task_id)},
        )
        await db.commit()
    # 连接删掉也不影响：没有钉住，测试环境照旧强制 Mock
    assert (await alice.delete(f"{CONN}/{a['id']}")).status_code == 204
    assert "upstream" not in dict((await _task(task_id)).input_json or {})
    assert await execute_task({}, task_id) == "succeeded"


async def test_platform_upstream_is_not_pinned(alice: AsyncClient, real_resolution: None) -> None:
    """平台路由（非 `provider.org:`）不钉：与不带 upstream 的解析完全一样，failover 照旧。"""
    org = await _org(alice)
    platform = {
        "capability": IMAGE,
        "provider_id": "provider.dashscope",
        "connection_id": None,
        "model_id": "wan2.2-t2i-plus",
        "layer": "platform",
        "key_source": "platform",
    }
    pinned = await gw._resolve(IMAGE, org_id=org, upstream=platform)
    plain = await gw._resolve(IMAGE, org_id=org)
    assert [(r.provider_id, r.model_id) for r in pinned.routes] == [
        (r.provider_id, r.model_id) for r in plain.routes
    ]
    assert pinned.key_source is plain.key_source


# ------------------------------------------------------------------ (b) 推理模型标记


async def _text_conn(client: AsyncClient, models: list[dict]) -> dict:
    resp = await client.post(
        CONN,
        json={
            "label": "文本网关",
            "base_url": "https://llm.example.invalid/v1",
            "models": models,
            "api_key": TEXT_KEY,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_reasoning_flag_round_trips_and_defaults_to_false(alice: AsyncClient) -> None:
    conn = await _text_conn(
        alice,
        [
            {"model_id": "fast", "protocol": "openai_chat"},  # 缺省
            {"model_id": "thinker", "protocol": "openai_chat", "reasoning": True},
        ],
    )
    assert [(m["model_id"], m["reasoning"]) for m in conn["models"]] == [
        ("fast", False),
        ("thinker", True),
    ]
    detail = (await alice.get(f"{CONN}/{conn['id']}")).json()
    assert [m["reasoning"] for m in detail["models"]] == [False, True]
    listed = (await alice.get(CONN)).json()["items"][0]
    assert [m["reasoning"] for m in listed["models"]] == [False, True]

    # 修改：翻转标记
    patched = await alice.patch(
        f"{CONN}/{conn['id']}",
        json={
            "models": [
                {"model_id": "fast", "protocol": "openai_chat", "reasoning": True},
                {"model_id": "thinker", "protocol": "openai_chat"},
            ]
        },
    )
    assert [m["reasoning"] for m in patched.json()["models"]] == [True, False]

    # 聚合视图的选项里同样带出；平台目录按代码里的推理模型集合
    body = (await alice.get(BASE)).json()
    text_item = next(i for i in body["items"] if i["capability"] == TEXT)
    opts = {p["provider_id"]: p for p in text_item["providers"]}
    assert [m["reasoning"] for m in opts[conn["provider_id"]]["models"]] == [True, False]
    deepseek = {m["model_id"]: m["reasoning"] for m in opts["provider.deepseek"]["models"]}
    assert deepseek == {"deepseek-chat": False, "deepseek-v4-flash": True}

    # 不是布尔值：422
    bad = await alice.patch(
        f"{CONN}/{conn['id']}",
        json={"models": [{"model_id": "x", "protocol": "openai_chat", "reasoning": {"a": 1}}]},
    )
    assert bad.status_code == 422


async def test_presets_carry_reasoning_and_copy_it_into_connections(alice: AsyncClient) -> None:
    presets = {p["preset_id"]: p for p in (await alice.get(f"{BASE}/presets")).json()["presets"]}
    deepseek = {m["model_id"]: m["reasoning"] for m in presets["deepseek"]["models"]}
    assert deepseek == {"deepseek-flash": False, "deepseek-v4-pro": True}
    assert all(m["reasoning"] is False for m in presets["zhipu_cogview"]["models"])

    created = await alice.post(CONN, json={"preset_id": "deepseek", "api_key": TEXT_KEY})
    assert created.status_code == 201
    assert {m["model_id"]: m["reasoning"] for m in created.json()["models"]} == deepseek


async def test_legacy_models_without_reasoning_key_read_as_false(alice: AsyncClient) -> None:
    """A3 之前存的条目没有 `reasoning` 键（迁移来的旧端点也是）：读出来是 false，解析照常。"""
    conn = await _text_conn(alice, [{"model_id": "m1", "protocol": "openai_chat"}])
    async with session_scope() as db:
        await db.execute(
            text(
                "UPDATE org_provider_connections SET models = "
                '\'[{"model_id": "m1", "protocol": "openai_chat"}]\'::jsonb WHERE id = :i'
            ),
            {"i": uuid.UUID(conn["id"])},
        )
        await db.commit()
    detail = (await alice.get(f"{CONN}/{conn['id']}")).json()
    assert detail["models"] == [
        {
            "model_id": "m1",
            "protocol": "openai_chat",
            "capability": TEXT,
            "consistency_verified": None,
            "reasoning": False,
        }
    ]
    await _set_default(alice, TEXT, conn, "m1")
    org = await _org(alice)
    (route,) = (await gw._resolve(TEXT, org_id=org, allow_reasoning=False)).routes
    assert route.model_id == "m1"


async def test_no_reasoning_roles_are_refused_a_reasoning_connection_model(
    alice: AsyncClient,
) -> None:
    conn = await _text_conn(
        alice,
        [
            {"model_id": "thinker", "protocol": "openai_chat", "reasoning": True},
            {"model_id": "fast", "protocol": "openai_chat"},
        ],
    )
    await _set_default(alice, TEXT, conn, "thinker")
    org = await _org(alice)

    # 其他角色放行：解析到这个连接的这个模型（只解析，不调用）
    (route,) = (await gw._resolve(TEXT, org_id=org, allow_reasoning=True)).routes
    assert (route.provider_id, route.model_id) == (conn["provider_id"], "thinker")

    # no_reasoning 角色：整条 generate_text 在发请求之前就拒绝，不换到 fast、不落平台
    with pytest.raises(AppError) as exc:
        await gw.generate_text(TextRequest(system="s", user="u"), org_id=org, allow_reasoning=False)
    assert exc.value.code == "provider.byok.rejected"
    assert exc.value.detail["reason"] == "reasoning_model_not_allowed"
    assert exc.value.detail["model_id"] == "thinker"

    # 选非推理模型就放行
    await _set_default(alice, TEXT, conn, "fast")
    (route,) = (await gw._resolve(TEXT, org_id=org, allow_reasoning=False)).routes
    assert route.model_id == "fast"


async def test_project_preference_on_reasoning_model_is_refused_not_dropped(
    alice: AsyncClient,
) -> None:
    """平台目录的推理模型偏好会被丢掉、按默认跑；连接的不行——那等于静默换模型。"""
    conn = await _text_conn(
        alice, [{"model_id": "thinker", "protocol": "openai_chat", "reasoning": True}]
    )
    pid = (await alice.post("/api/v1/projects", json={"title": "p"})).json()["id"]
    resp = await alice.patch(
        f"/api/v1/projects/{pid}/model-preference",
        json={"capability": TEXT, "model_id": f"{conn['provider_id']}:thinker"},
    )
    assert resp.status_code == 200
    org = await _org(alice)
    with pytest.raises(AppError) as exc:
        await gw.generate_text(
            TextRequest(system="s", user="u"),
            org_id=org,
            project_id=uuid.UUID(pid),
            allow_reasoning=False,
        )
    assert exc.value.detail["reason"] == "reasoning_model_not_allowed"


# ------------------------------------------------------------------ (d) 连接引用


async def test_references_list_defaults_and_live_projects(alice: AsyncClient) -> None:
    conn = await _text_conn(alice, [{"model_id": "m1", "protocol": "openai_chat"}])
    other = await _text_conn(alice, [{"model_id": "m2", "protocol": "openai_chat"}])
    img = await _image_conn(alice, label="A", host="a", key=KEY_A)

    empty = await alice.get(f"{CONN}/{conn['id']}/references")
    assert empty.status_code == 200 and empty.json() == {"defaults": [], "projects": []}

    await _set_default(alice, TEXT, conn, None)
    await _set_default(alice, IMAGE, img, None)  # 指向别的连接，不算

    async def _project(title: str, ref: str) -> str:
        pid = str((await alice.post("/api/v1/projects", json={"title": title})).json()["id"])
        resp = await alice.patch(
            f"/api/v1/projects/{pid}/model-preference", json={"capability": TEXT, "model_id": ref}
        )
        assert resp.status_code == 200, resp.text
        return pid

    with_model = await _project("带模型", f"{conn['provider_id']}:m1")
    bare = await _project("不带模型", conn["provider_id"])
    gone = await _project("已删", conn["provider_id"])
    await _project("别的连接", f"{other['provider_id']}:m2")
    assert (await alice.delete(f"/api/v1/projects/{gone}")).status_code == 204

    refs = (await alice.get(f"{CONN}/{conn['id']}/references")).json()
    assert refs["defaults"] == [{"capability": TEXT}]
    assert sorted(
        (p["project_id"], p["name"], p["capability"]) for p in refs["projects"]
    ) == sorted([(with_model, "带模型", TEXT), (bare, "不带模型", TEXT)])

    img_refs = (await alice.get(f"{CONN}/{img['id']}/references")).json()
    assert img_refs == {"defaults": [{"capability": IMAGE}], "projects": []}


async def test_references_are_tenant_isolated(alice: AsyncClient, bob: AsyncClient) -> None:
    conn = await _text_conn(alice, [{"model_id": "m1", "protocol": "openai_chat"}])
    assert (await bob.get(f"{CONN}/{conn['id']}/references")).status_code == 404
    assert (await alice.get(f"{CONN}/{uuid.uuid4()}/references")).status_code == 404
    # 已删的连接同样 404（与详情接口一致）
    assert (await alice.delete(f"{CONN}/{conn['id']}")).status_code == 204
    assert (await alice.get(f"{CONN}/{conn['id']}/references")).status_code == 404
