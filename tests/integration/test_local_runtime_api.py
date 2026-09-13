"""本机运行时的四个端点。

这套用例的重点全在**边界**上，不在"能不能跑通"：

* 默认关着的时候，桥接端点连存在都不该确认（404）。
* 桥接令牌只开 poll / heartbeat / result 三条路径，换不出任何项目接口的访问权。
* `status` 只说本 org 的事——别的租户配了本机运行时，这里必须一无所知。
* 结果是一次性的：重放、租约不符、已清理，一律 409，不覆盖任何东西。
* 回传的图片必须**真的是一张图**：任意路径、URL、一段文字都不许进资产库。
"""

from __future__ import annotations

import asyncio
import base64
import io
import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from PIL import Image

from apps.api.core.config import Settings
from apps.api.modules.local_runtime import router as lr_router
from apps.api.modules.local_runtime import service as lr_service
from apps.api.modules.local_runtime import transport

pytestmark = pytest.mark.integration

BASE = "/api/v1/local-runtime"
TOKEN = "bridge-" + "k" * 40

#: 一张**真的** PNG（Pillow 生成，能解出像素）。
#:
#: 手写"魔数 + IHDR 头"那种假 PNG 在这里不够用了：入库前会用 Pillow 真解一遍，
#: 因为魔数只有八个字节，"PNG 头 + 一坨随机数据"能轻松骗过它。


def _png(width: int = 64, height: int = 64) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (12, 34, 56)).save(buf, format="PNG")
    return buf.getvalue()


PNG = _png()

#: 头是对的，内容是坏的。魔数挡不住它，Pillow 能。
CORRUPT_PNG = PNG[:40] + bytes(200)


async def _new_project(client: AsyncClient, title: str = "本机运行时试点") -> uuid.UUID:
    resp = await client.post("/api/v1/projects", json={"title": title})
    assert resp.status_code == 201, resp.text
    return uuid.UUID(resp.json()["id"])


async def _org_id(client: AsyncClient) -> uuid.UUID:
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 200, resp.text
    return uuid.UUID(resp.json()["org_id"])


def _settings_factory(org_id: uuid.UUID, project_ids: list[uuid.UUID], provider: str = "codex"):
    kwargs: dict[str, object] = {
        "env": "test",
        "local_cli_enabled": True,
        "local_cli_org_id": org_id,
        "local_cli_project_ids": project_ids,
        "local_cli_provider": provider,
        "local_cli_token": TOKEN,
        "local_cli_timeout_seconds": 30,
        "local_cli_image_timeout_seconds": 60,
    }
    settings = Settings(**kwargs)  # type: ignore[arg-type]
    return lambda: settings


@pytest.fixture
def enable_pilot(monkeypatch: pytest.MonkeyPatch):
    """把试点打开。

    `get_settings` 在 router 与 service 里都是**导入时绑定的函数对象**，
    只 patch `core.config` 上那一个不会生效——两边都要打。
    """

    def _enable(org_id: uuid.UUID, project_ids: list[uuid.UUID], provider: str = "codex") -> None:
        factory = _settings_factory(org_id, project_ids, provider)
        monkeypatch.setattr(lr_router, "get_settings", factory)
        monkeypatch.setattr(lr_service, "get_settings", factory)

    return _enable


@pytest.fixture
async def clean_mailbox() -> AsyncIterator[None]:
    """用例之间不留队列残渣。"""
    yield
    for provider in ("codex", "claude"):
        r = transport._r()
        try:
            await r.delete(
                transport.queue_key(provider, "text"),
                transport.queue_key(provider, "image"),
                transport.heartbeat_key(provider),
            )
        finally:
            await transport._release(r)


def _auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _poll_body(kinds: list[str] | None = None, provider: str = "codex") -> dict:
    return {"provider": provider, "kinds": kinds or ["text", "image"]}


# ---------------------------------------------------------------- 默认关着


class TestDisabledByDefault:
    async def test_poll_is_not_even_acknowledged(self, alice: AsyncClient) -> None:
        r = await alice.post(f"{BASE}/poll", json=_poll_body(), headers=_auth())
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "common.not_found"

    async def test_heartbeat_is_not_even_acknowledged(self, alice: AsyncClient) -> None:
        r = await alice.post(f"{BASE}/heartbeat", json=_poll_body(), headers=_auth())
        assert r.status_code == 404

    async def test_result_is_not_even_acknowledged(self, alice: AsyncClient) -> None:
        r = await alice.post(
            f"{BASE}/requests/{uuid.uuid4()}/result",
            json={"lease_token": "x" * 32, "text": "hi"},
            headers=_auth(),
        )
        assert r.status_code == 404

    async def test_status_is_mounted_and_honest(self, alice: AsyncClient) -> None:
        """状态端点必须一直挂着：界面靠它决定要不要显示这块。"""
        r = await alice.get(f"{BASE}/status")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["enabled"] is False
        assert body["project_ids"] == []
        assert body["text_provider"] is None
        assert body["pilot"] is True
        # 关着的时候一种能力都没有——写 True 就是一个点下去必然失败的入口。
        assert body["capabilities"] == {
            "text": False,
            "image": False,
            "video": False,
            "audio": False,
        }
        assert body["image_available"] is False
        assert body["image_unavailable_reason"]


# ---------------------------------------------------------------- 鉴权


class TestBridgeAuth:
    async def test_missing_and_wrong_tokens_are_rejected(
        self, alice: AsyncClient, enable_pilot, clean_mailbox: None
    ) -> None:
        enable_pilot(await _org_id(alice), [await _new_project(alice)])

        r = await alice.post(f"{BASE}/poll", json=_poll_body())
        assert r.status_code == 401

        r = await alice.post(f"{BASE}/poll", json=_poll_body(), headers=_auth("wrong-" + "k" * 40))
        assert r.status_code == 401

        # 非 ASCII 的令牌也必须是 401 而不是 500——`compare_digest` 对
        # 非 ASCII 的 str 会抛 TypeError，两边都先编码才不会把一次鉴权失败
        # 变成未捕获异常。
        #
        # 头必须以 **bytes** 传：HTTP 头在线上本来就是字节，而 httpx 拒绝
        # 把非 ASCII 的 str 编码进头里（客户端就报错了，根本到不了服务端，
        # 也就测不到服务端到底会不会 500）。
        r = await alice.post(
            f"{BASE}/poll",
            json=_poll_body(),
            headers={"Authorization": ("Bearer " + "é" * 40).encode("latin-1")},
        )
        assert r.status_code == 401

    async def test_bridge_token_cannot_reach_project_apis(
        self, alice: AsyncClient, enable_pilot, clean_mailbox: None
    ) -> None:
        """桥接令牌不是登录态。它只开三条路径，别的接口一律不认。"""
        enable_pilot(await _org_id(alice), [await _new_project(alice)])
        from httpx import ASGITransport

        from apps.api.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as anonymous:
            r = await anonymous.get("/api/v1/projects", headers=_auth())
            assert r.status_code == 401
            r = await anonymous.get(f"{BASE}/status", headers=_auth())
            assert r.status_code == 401

    async def test_provider_mismatch_is_refused(
        self, alice: AsyncClient, enable_pilot, clean_mailbox: None
    ) -> None:
        """配的文本是 codex、图片也是 codex，claude 不许来抢单——
        抢走了用户拿到的就是另一个模型。"""
        enable_pilot(await _org_id(alice), [await _new_project(alice)], provider="codex")
        r = await alice.post(f"{BASE}/poll", json=_poll_body(provider="claude"), headers=_auth())
        assert r.status_code == 403


# ---------------------------------------------------------------- 状态


class TestStatusScoping:
    async def test_configured_org_sees_its_own_projects(
        self, alice: AsyncClient, enable_pilot, clean_mailbox: None
    ) -> None:
        project_id = await _new_project(alice)
        enable_pilot(await _org_id(alice), [project_id])

        r = await alice.get(f"{BASE}/status")
        body = r.json()
        assert body["enabled"] is True
        assert body["text_provider"] == "codex"
        assert body["image_provider"] == "codex"
        assert body["project_ids"] == [str(project_id)]
        assert body["runner_connected"] is False
        assert body["image_available"] is False

        # poll 一次就算连上了，能力按连接器自报的算
        await alice.post(f"{BASE}/poll", json=_poll_body(["text", "image"]), headers=_auth())
        body = (await alice.get(f"{BASE}/status")).json()
        assert body["runner_connected"] is True
        assert body["capabilities"]["image"] is True
        assert body["image_available"] is True
        assert body["image_unavailable_reason"] is None

    async def test_a_text_only_runner_does_not_light_up_image(
        self, alice: AsyncClient, enable_pilot, clean_mailbox: None
    ) -> None:
        """连接器只报了文本，界面上就不能显示"可以本机出图"，
        而且要说清楚为什么不能。"""
        enable_pilot(await _org_id(alice), [await _new_project(alice)])
        await alice.post(f"{BASE}/poll", json=_poll_body(["text"]), headers=_auth())
        body = (await alice.get(f"{BASE}/status")).json()
        assert body["capabilities"]["image"] is False
        assert body["image_available"] is False
        assert "出图" in body["image_unavailable_reason"]

    async def test_other_tenants_learn_nothing(
        self, alice: AsyncClient, bob: AsyncClient, enable_pilot, clean_mailbox: None
    ) -> None:
        project_id = await _new_project(alice)
        enable_pilot(await _org_id(alice), [project_id])

        body = (await bob.get(f"{BASE}/status")).json()
        assert body["enabled"] is False
        assert body["project_ids"] == []
        assert str(project_id) not in str(body)

    async def test_a_project_id_from_another_org_is_not_echoed(
        self, alice: AsyncClient, bob: AsyncClient, enable_pilot, clean_mailbox: None
    ) -> None:
        """配置里写错一个 id（写成别人的项目）也不能被回显出去。"""
        alice_project = await _new_project(alice)
        bob_project = await _new_project(bob)
        enable_pilot(await _org_id(alice), [alice_project, bob_project])

        body = (await alice.get(f"{BASE}/status")).json()
        assert body["project_ids"] == [str(alice_project)]


# ---------------------------------------------------------------- 收发闭环


class TestRoundTrip:
    async def test_poll_returns_204_when_idle(
        self, alice: AsyncClient, enable_pilot, clean_mailbox: None
    ) -> None:
        enable_pilot(await _org_id(alice), [await _new_project(alice)])
        r = await alice.post(f"{BASE}/poll", json=_poll_body(), headers=_auth())
        assert r.status_code == 204
        assert not r.content

    async def test_request_reaches_the_runner_and_the_answer_comes_back(
        self, alice: AsyncClient, enable_pilot, clean_mailbox: None
    ) -> None:
        org_id, project_id = await _org_id(alice), await _new_project(alice)
        enable_pilot(org_id, [project_id])
        await transport.touch_heartbeat("codex", kinds=["text"])

        async def _generate() -> lr_service.LocalCompletion:
            return await lr_service.complete_text(
                org_id=org_id,
                project_id=project_id,
                system="系统提示词",
                user="用户输入",
                schema_name="StoryOutline",
                max_output_tokens=1024,
            )

        task = asyncio.create_task(_generate())
        try:
            leased = await _poll_until_work(alice)
            assert leased["kind"] == "text"
            assert leased["text"]["system"] == "系统提示词"
            assert leased["text"]["user"] == "用户输入"
            assert leased["text"]["schema_name"] == "StoryOutline"

            r = await alice.post(
                f"{BASE}/requests/{leased['request_id']}/result",
                json={
                    "lease_token": leased["lease_token"],
                    "text": '{"title": "雾港迷案"}',
                    "model_id": "gpt-5.1-codex",
                    "tokens_in": 120,
                    "tokens_out": 40,
                },
                headers=_auth(),
            )
            assert r.status_code == 204, r.text
            result = await asyncio.wait_for(task, timeout=20)
        finally:
            task.cancel()

        assert result.text == '{"title": "雾港迷案"}'
        assert result.model_id == "gpt-5.1-codex"
        assert (result.tokens_in, result.tokens_out) == (120, 40)

    async def test_an_image_round_trip_returns_real_bytes(
        self, alice: AsyncClient, enable_pilot, clean_mailbox: None
    ) -> None:
        """出图这条：提示词由服务端合成、连接器回 base64、服务端按魔数验。"""
        org_id, project_id = await _org_id(alice), await _new_project(alice)
        enable_pilot(org_id, [project_id])
        await transport.touch_heartbeat("codex", kinds=["image"])

        task = asyncio.create_task(
            lr_service.complete_image(
                org_id=org_id,
                project_id=project_id,
                prompt="水墨风格，雨夜码头，空场景",
                negative_prompt="文字水印",
            )
        )
        try:
            leased = await _poll_until_work(alice)
            assert leased["kind"] == "image"
            assert leased["image"]["prompt"] == "水墨风格，雨夜码头，空场景"
            assert leased["image"]["negative_prompt"] == "文字水印"
            # 提示词是服务端合成好的，连接器不需要（也不该）再拿到风格档案。
            assert leased["text"] is None

            r = await alice.post(
                f"{BASE}/requests/{leased['request_id']}/result",
                json={
                    "lease_token": leased["lease_token"],
                    "image_base64": base64.b64encode(PNG).decode("ascii"),
                    "image_mime": "image/png",
                    "revised_prompt": "模型改写后的提示词",
                    "model_id": "local-cli.codex",
                },
                headers=_auth(),
            )
            assert r.status_code == 204, r.text
            image = await asyncio.wait_for(task, timeout=20)
        finally:
            task.cancel()

        assert image.data == PNG
        assert image.mime_type == "image/png"
        assert image.revised_prompt == "模型改写后的提示词"

    async def test_a_replayed_result_cannot_overwrite(
        self, alice: AsyncClient, enable_pilot, clean_mailbox: None
    ) -> None:
        org_id, project_id = await _org_id(alice), await _new_project(alice)
        enable_pilot(org_id, [project_id])
        await transport.touch_heartbeat("codex", kinds=["text"])

        task = asyncio.create_task(
            lr_service.complete_text(
                org_id=org_id,
                project_id=project_id,
                system="s",
                user="u",
                schema_name="StoryOutline",
                max_output_tokens=256,
            )
        )
        try:
            leased = await _poll_until_work(alice)
            body = {"lease_token": leased["lease_token"], "text": "第一次"}
            first = await alice.post(
                f"{BASE}/requests/{leased['request_id']}/result", json=body, headers=_auth()
            )
            assert first.status_code == 204
            result = await asyncio.wait_for(task, timeout=20)

            replay = await alice.post(
                f"{BASE}/requests/{leased['request_id']}/result",
                json={"lease_token": leased["lease_token"], "text": "第二次"},
                headers=_auth(),
            )
            assert replay.status_code == 409
        finally:
            task.cancel()
        assert result.text == "第一次"

    async def test_a_wrong_lease_token_is_refused(
        self, alice: AsyncClient, enable_pilot, clean_mailbox: None
    ) -> None:
        org_id, project_id = await _org_id(alice), await _new_project(alice)
        enable_pilot(org_id, [project_id])
        await transport.touch_heartbeat("codex", kinds=["text"])

        task = asyncio.create_task(
            lr_service.complete_text(
                org_id=org_id,
                project_id=project_id,
                system="s",
                user="u",
                schema_name="StoryOutline",
                max_output_tokens=256,
            )
        )
        try:
            leased = await _poll_until_work(alice)
            r = await alice.post(
                f"{BASE}/requests/{leased['request_id']}/result",
                json={"lease_token": "y" * 64, "text": "冒名顶替"},
                headers=_auth(),
            )
            assert r.status_code == 409
        finally:
            task.cancel()

    async def test_unknown_request_id_is_refused(
        self, alice: AsyncClient, enable_pilot, clean_mailbox: None
    ) -> None:
        enable_pilot(await _org_id(alice), [await _new_project(alice)])
        r = await alice.post(
            f"{BASE}/requests/{uuid.uuid4()}/result",
            json={"lease_token": "y" * 64, "text": "凭空捏造"},
            headers=_auth(),
        )
        assert r.status_code == 409

    async def test_result_must_be_exactly_one_of_text_image_or_error(
        self, alice: AsyncClient, enable_pilot, clean_mailbox: None
    ) -> None:
        enable_pilot(await _org_id(alice), [await _new_project(alice)])
        url = f"{BASE}/requests/{uuid.uuid4()}/result"

        both = await alice.post(
            url,
            json={"lease_token": "y" * 64, "text": "有", "error_code": "timeout"},
            headers=_auth(),
        )
        assert both.status_code == 422

        text_and_image = await alice.post(
            url,
            json={
                "lease_token": "y" * 64,
                "text": "有",
                "image_base64": base64.b64encode(PNG).decode("ascii"),
            },
            headers=_auth(),
        )
        assert text_and_image.status_code == 422

        neither = await alice.post(url, json={"lease_token": "y" * 64}, headers=_auth())
        assert neither.status_code == 422

        blank = await alice.post(
            url, json={"lease_token": "y" * 64, "text": "   "}, headers=_auth()
        )
        assert blank.status_code == 422

    async def test_unbounded_error_codes_are_rejected_by_the_schema(
        self, alice: AsyncClient, enable_pilot, clean_mailbox: None
    ) -> None:
        """错误码是有界枚举。放开它等于允许把 stderr 原文塞进来。"""
        enable_pilot(await _org_id(alice), [await _new_project(alice)])
        r = await alice.post(
            f"{BASE}/requests/{uuid.uuid4()}/result",
            json={
                "lease_token": "y" * 64,
                "error_code": r"Traceback ... C:\Users\alice\.codex\auth.json",
            },
            headers=_auth(),
        )
        assert r.status_code == 422


# ---------------------------------------------------------------- 图片必须是图片


class TestImageBytesAreValidated:
    @pytest.mark.parametrize(
        "payload",
        [
            # 一个本地路径。`savedPath` 那个字段的教训：路径是对端说的，
            # 它可以指向 auth.json、指向仓库、指向任何东西。
            base64.b64encode(rb"C:\Users\alice\.codex\auth.json").decode("ascii"),
            # 一个 URL。上游 URL 只有 24 小时有效期，且我们从不下载对端给的链接。
            base64.b64encode(b"https://evil.example/whatever.png").decode("ascii"),
            # 一段普通文字。宽松 base64 解码能把它解出"字节"来。
            base64.b64encode("这不是图片，只是一段话".encode()).decode("ascii"),
            # RIFF 容器但不是 WEBP（比如 wav）。
            base64.b64encode(b"RIFF" + bytes(4) + b"WAVEfmt ").decode("ascii"),
            # **头是对的、内容是坏的**。魔数只有八个字节，挡不住它；
            # 这一条正是"必须真解一遍"的理由。
            base64.b64encode(CORRUPT_PNG).decode("ascii"),
            # 根本不是 base64。
            "!!!这不是 base64!!!",
        ],
    )
    async def test_anything_that_is_not_an_image_is_refused(
        self, alice: AsyncClient, enable_pilot, clean_mailbox: None, payload: str
    ) -> None:
        org_id, project_id = await _org_id(alice), await _new_project(alice)
        enable_pilot(org_id, [project_id])
        await transport.touch_heartbeat("codex", kinds=["image"])

        task = asyncio.create_task(
            lr_service.complete_image(org_id=org_id, project_id=project_id, prompt="画一张")
        )
        try:
            leased = await _poll_until_work(alice)
            r = await alice.post(
                f"{BASE}/requests/{leased['request_id']}/result",
                json={"lease_token": leased["lease_token"], "image_base64": payload},
                headers=_auth(),
            )
            # 422（schema 挡住的）或 502（服务端解码时挡住的）都算挡住了，
            # 但**绝不能**是 204。
            assert r.status_code in (422, 502), r.text

            # 而且等待方要立刻失败，不能傻等到超时。
            from apps.api.core.errors import AppError

            with pytest.raises(AppError) as exc:
                await asyncio.wait_for(task, timeout=20)
            assert exc.value.code.startswith("local_runtime.")
        finally:
            task.cancel()


# ---------------------------------------------------------------- 心跳与取消


class TestHeartbeatEndpoint:
    async def test_heartbeat_keeps_the_runner_alive_without_taking_work(
        self, alice: AsyncClient, enable_pilot, clean_mailbox: None
    ) -> None:
        org_id, project_id = await _org_id(alice), await _new_project(alice)
        enable_pilot(org_id, [project_id])

        r = await alice.post(f"{BASE}/heartbeat", json=_poll_body(), headers=_auth())
        assert r.status_code == 200, r.text
        assert r.json() == {"outstanding": True}
        assert (await alice.get(f"{BASE}/status")).json()["runner_connected"] is True

        # 心跳不出队：忙着的连接器不能再拿一条活。
        await transport.enqueue(
            provider="codex",
            kind="text",
            request_id=uuid.uuid4(),
            timeout_seconds=30,
            text=transport.TextJob(system="s", user="u", schema_name="X", max_output_tokens=8),
        )
        again = await alice.post(f"{BASE}/heartbeat", json=_poll_body(), headers=_auth())
        assert again.json() == {"outstanding": True}
        assert await transport.queue_depth("codex", "text") == 1

    async def test_heartbeat_tells_the_runner_to_stop_after_cleanup(
        self, alice: AsyncClient, enable_pilot, clean_mailbox: None
    ) -> None:
        """用户取消 / 等待方超时之后，连接器必须立刻停手——
        继续跑烧的是他自己的订阅额度，而结果没有人会收。"""
        org_id, project_id = await _org_id(alice), await _new_project(alice)
        enable_pilot(org_id, [project_id])
        await transport.touch_heartbeat("codex", kinds=["text"])

        task = asyncio.create_task(
            lr_service.complete_text(
                org_id=org_id,
                project_id=project_id,
                system="s",
                user="u",
                schema_name="StoryOutline",
                max_output_tokens=256,
            )
        )
        try:
            leased = await _poll_until_work(alice)
            alive = await alice.post(
                f"{BASE}/heartbeat",
                json={**_poll_body(), "request_id": leased["request_id"]},
                headers=_auth(),
            )
            assert alive.json() == {"outstanding": True}

            # 等待方走了（取消/超时），清理把租约删掉。
            task.cancel()
            await asyncio.sleep(0.2)
            await transport.cleanup(
                provider="codex", kind="text", request_id=uuid.UUID(leased["request_id"])
            )

            dead = await alice.post(
                f"{BASE}/heartbeat",
                json={**_poll_body(), "request_id": leased["request_id"]},
                headers=_auth(),
            )
            assert dead.json() == {"outstanding": False}
        finally:
            task.cancel()


class TestFailFast:
    async def test_no_runner_means_immediate_failure(
        self, alice: AsyncClient, enable_pilot, clean_mailbox: None
    ) -> None:
        """连接器没在跑就立刻失败。让用户干等三分钟再看到超时，
        他只会以为是模型慢。"""
        from apps.api.core.errors import AppError

        org_id, project_id = await _org_id(alice), await _new_project(alice)
        enable_pilot(org_id, [project_id])

        r = transport._r()
        try:
            await r.delete(transport.heartbeat_key("codex"))
        finally:
            await transport._release(r)

        with pytest.raises(AppError) as exc:
            await lr_service.complete_text(
                org_id=org_id,
                project_id=project_id,
                system="s",
                user="u",
                schema_name="StoryOutline",
                max_output_tokens=256,
            )
        assert exc.value.code == "provider.unavailable"

        with pytest.raises(AppError) as img:
            await lr_service.complete_image(org_id=org_id, project_id=project_id, prompt="画一张")
        assert img.value.code == "local_runtime.offline"

    async def test_a_project_outside_the_whitelist_is_refused(
        self, alice: AsyncClient, enable_pilot, clean_mailbox: None
    ) -> None:
        from apps.api.core.errors import AppError

        org_id = await _org_id(alice)
        enable_pilot(org_id, [await _new_project(alice)])
        with pytest.raises(AppError) as exc:
            await lr_service.complete_text(
                org_id=org_id,
                project_id=uuid.uuid4(),
                system="s",
                user="u",
                schema_name="StoryOutline",
                max_output_tokens=256,
            )
        assert exc.value.code == "common.forbidden"

    async def test_another_org_cannot_ride_the_same_runner(
        self, alice: AsyncClient, bob: AsyncClient, enable_pilot, clean_mailbox: None
    ) -> None:
        """连接器是**一个 org 的**。别的租户即使拿着同一个项目 id 也进不来——
        邮箱是全局的一条队列，隔离必须在入队之前就判死。"""
        from apps.api.core.errors import AppError

        alice_org, project_id = await _org_id(alice), await _new_project(alice)
        enable_pilot(alice_org, [project_id])
        await transport.touch_heartbeat("codex", kinds=["image", "text"])

        bob_org = await _org_id(bob)
        with pytest.raises(AppError) as exc:
            await lr_service.complete_image(org_id=bob_org, project_id=project_id, prompt="画一张")
        assert exc.value.code == "local_runtime.not_configured"
        # 队列里一条都没有：拦在入队之前，不是入队之后再撤。
        assert await transport.queue_depth("codex", "image") == 0


class TestCancelLifecycle:
    """P2-5：把「网页上取消 → 桌面停手」这条链路的两头补上。

    此前有测试的只有中间两截（`transport.is_outstanding`、心跳回
    `outstanding:false`）。两头——服务端等待时会不会真的去问一句"取消了吗"、
    Worker 那个 `_cancel_watcher` 到底读没读 `tasks.status`——一行都没有。
    而这正是本机这条路径的卖点：不停手就一直在烧用户自己的订阅额度。
    """

    async def test_the_image_wait_gives_up_as_soon_as_the_task_is_cancelled(
        self, alice: AsyncClient, enable_pilot, clean_mailbox: None
    ) -> None:
        """取消之后**远早于超时**就抛出，并且租约/队列都清干净了。

        超时是 60 秒，这条用例几秒就该结束——它等的是"取消"，不是超时。
        """
        from apps.api.core.errors import AppError

        org_id, project_id = await _org_id(alice), await _new_project(alice)
        enable_pilot(org_id, [project_id])
        await transport.touch_heartbeat("codex", kinds=["image"])

        asked = 0

        async def _is_cancelled() -> bool:
            # 第一段还没取消（模拟用户在中途才按下取消），第二段起就取消了。
            nonlocal asked
            asked += 1
            return asked >= 2

        started = asyncio.get_running_loop().time()
        with pytest.raises(AppError) as exc:
            await lr_service.complete_image(
                org_id=org_id,
                project_id=project_id,
                prompt="画一张",
                is_cancelled=_is_cancelled,
            )
        elapsed = asyncio.get_running_loop().time() - started

        assert exc.value.code == "local_runtime.failed"
        assert asked >= 2, "分段等待必须**在段间**真的问一次，否则取消永远等到超时"
        assert elapsed < 40, f"取消之后不该继续等满 60 秒超时（实际 {elapsed:.1f}s）"

        # 队列清了：留在队列里的请求会被下一次 poll 捞去再跑一遍，
        # 而用户那边早已看到失败。清理由 complete_image 的 finally 负责。
        assert await transport.queue_depth("codex", "image") == 0

    async def test_a_request_that_is_never_cancelled_still_waits_for_its_answer(
        self, alice: AsyncClient, enable_pilot, clean_mailbox: None
    ) -> None:
        """这道闸不能把正常路径变成"一问就退"。"""
        org_id, project_id = await _org_id(alice), await _new_project(alice)
        enable_pilot(org_id, [project_id])
        await transport.touch_heartbeat("codex", kinds=["image"])

        async def _never() -> bool:
            return False

        task = asyncio.create_task(
            lr_service.complete_image(
                org_id=org_id, project_id=project_id, prompt="画一张", is_cancelled=_never
            )
        )
        try:
            leased = await _poll_until_work(alice)
            done = await alice.post(
                f"{BASE}/requests/{leased['request_id']}/result",
                json={
                    "lease_token": leased["lease_token"],
                    "image_base64": base64.b64encode(PNG).decode("ascii"),
                    "image_mime": "image/png",
                },
                headers=_auth(),
            )
            assert done.status_code == 204, done.text
            image = await asyncio.wait_for(task, timeout=30)
            assert image.data == PNG
        finally:
            task.cancel()

    async def test_the_text_wait_is_sliced_too(
        self, alice: AsyncClient, enable_pilot, clean_mailbox: None
    ) -> None:
        """P3-14：文本那条也走分段等待，不再一次 BLPOP 占住共享连接三分钟。

        断言取"它真的在段间回头问了取消"——那是分段的可观测证据。
        """
        from apps.api.core.errors import AppError

        org_id, project_id = await _org_id(alice), await _new_project(alice)
        enable_pilot(org_id, [project_id])
        await transport.touch_heartbeat("codex", kinds=["text"])

        asked = 0

        async def _is_cancelled() -> bool:
            nonlocal asked
            asked += 1
            return asked >= 2

        with pytest.raises(AppError) as exc:
            await lr_service.complete_text(
                org_id=org_id,
                project_id=project_id,
                system="s",
                user="u",
                schema_name="StoryOutline",
                max_output_tokens=256,
                is_cancelled=_is_cancelled,
            )
        # 文本那条替代的是一次 Provider 调用，所以映射到 `provider.*`
        # 而不是 `local_runtime.*`——两组的文案面向的人不一样。
        assert exc.value.code == "provider.unavailable"
        assert asked >= 2
        assert await transport.queue_depth("codex", "text") == 0

    async def test_the_worker_watcher_reads_the_task_status_and_nothing_else(
        self, alice: AsyncClient, enable_pilot, clean_mailbox: None
    ) -> None:
        """`_cancel_watcher` 全仓零引用过。它读的必须是 `tasks.status`（ADR-008），
        不是另建一份状态。"""
        from apps.api.core.db import session_scope
        from apps.api.modules.task import service as task_service
        from worker.jobs import generation

        org_id, project_id = await _org_id(alice), await _new_project(alice)
        enable_pilot(org_id, [project_id])

        async with session_scope() as db:
            task, _created = await task_service.create_task(
                db,
                org_id=org_id,
                project_id=project_id,
                created_by=org_id,
                task_type="image.generate",
                input_json={"prompt": "画一张", "n": 1},
            )
        task_id = task.id

        watcher = generation._cancel_watcher(org_id=org_id, task_id=task_id)
        assert watcher is not None
        assert await watcher() is False, "queued 的任务还没被取消"

        async with session_scope() as db:
            await task_service.cancel_task(db, org_id=org_id, task_id=task_id)
        assert await watcher() is True, "取消之后桌面必须立刻停手"

        # 没有 task_id 的调用（例如从资产库直接生成）不该凭空造一个 watcher。
        assert generation._cancel_watcher(org_id=org_id, task_id=None) is None

    async def test_cancelling_twice_does_not_move_money_twice(
        self, alice: AsyncClient, enable_pilot, clean_mailbox: None
    ) -> None:
        """取消要幂等到账本上：一次取消对应一次释放，不是两次。"""
        from sqlalchemy import func, select

        from apps.api.core.db import session_scope
        from apps.api.core.errors import AppError
        from apps.api.modules.billing.models import CreditAccount, CreditTransaction
        from apps.api.modules.task import service as task_service

        org_id, project_id = await _org_id(alice), await _new_project(alice)
        enable_pilot(org_id, [project_id])

        async with session_scope() as db:
            task, _created = await task_service.create_task(
                db,
                org_id=org_id,
                project_id=project_id,
                created_by=org_id,
                task_type="image.generate",
                input_json={"prompt": "画一张", "n": 1},
            )
        task_id = task.id

        async def _count() -> int:
            async with session_scope() as db:
                return int(
                    (
                        await db.execute(
                            select(func.count())
                            .select_from(CreditTransaction)
                            .join(CreditAccount, CreditAccount.id == CreditTransaction.account_id)
                            .where(CreditAccount.org_id == org_id)
                        )
                    ).scalar_one()
                )

        async with session_scope() as db:
            await task_service.cancel_task(db, org_id=org_id, task_id=task_id)
        after_first = await _count()

        with pytest.raises(AppError):
            async with session_scope() as db:
                await task_service.cancel_task(db, org_id=org_id, task_id=task_id)
        assert await _count() == after_first, "第二次取消不该再动一次账本"


async def _poll_until_work(client: AsyncClient, *, attempts: int = 50) -> dict:
    """等到取到一条请求。入队发生在另一个协程里，可能还没轮到它跑。"""
    for _ in range(attempts):
        r = await client.post(f"{BASE}/poll", json=_poll_body(), headers=_auth())
        assert r.status_code in (200, 204), r.text
        if r.status_code == 200:
            return dict(r.json())
        await asyncio.sleep(0.05)
    raise AssertionError("一直没取到请求")
