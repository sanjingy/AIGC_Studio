"""项目级模型偏好：从"在界面上选一次"到"上游真的收到这个模型"（ADR-024）。

`tests/unit/test_gateway_model_preference.py` 把重排逻辑单测了，
但它把查库那一环换成了假件。这里补的正是被换掉的那一环——**真的走 HTTP
存偏好、真的落 JSONB、真的被 Gateway 读出来、真的传到适配器手里**。

这正是 ADR-024 一直缺的那一环：决定拍了、列也建好了，中间没有任何东西
把"用户选了什么"和"这次调用用了什么"接上。只测其中一头都发现不了。

适配器仍然是假的：真打上游要花钱，而且证明不了什么——能在假适配器里看到
`model_id`，就说明偏好确实走完了全程。
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any, ClassVar

import pytest
from httpx import AsyncClient

from adapters.providers.base import KeySource, TextRequest, TextResponse
from apps.api.modules.gateway import breaker, catalog
from apps.api.modules.gateway import service as gw

pytestmark = pytest.mark.integration

CAPABILITY = "text_generation"
REAL_PROVIDER = "provider.deepseek"
DEFAULT_MODEL = "deepseek-chat"  # 目录里优先级最高的那个
OTHER_MODEL = "deepseek-v4-flash"
PLATFORM_KEY = "sk-platform-key-for-preference-test"


class RecordingProvider:
    """假适配器。链路的终点：能在这里看到 model_id，就说明偏好真的生效了。"""

    provider_id = REAL_PROVIDER
    calls: ClassVar[list[str]] = []

    def __init__(
        self, *, api_key: str, model_id: str, key_source: KeySource = KeySource.PLATFORM
    ) -> None:
        self._api_key = api_key
        self.model_id = model_id
        self.key_source = key_source

    async def generate_text(self, request: TextRequest) -> TextResponse:
        del request
        RecordingProvider.calls.append(self.model_id)
        return TextResponse(text='{"ok": true}', tokens_in=1, tokens_out=1, model_id=self.model_id)


@pytest.fixture
def fake_adapter(monkeypatch: pytest.MonkeyPatch) -> Any:
    """只把"发 HTTP 出去"换掉，能力→Provider→模型的绑定保持真实。"""
    RecordingProvider.calls = []
    spec = catalog.spec_for(REAL_PROVIDER, capability=CAPABILITY)
    assert spec is not None
    monkeypatch.setattr(catalog, "SPECS", (dataclasses.replace(spec, adapter=RecordingProvider),))
    monkeypatch.setattr(catalog, "platform_key", lambda provider_id: PLATFORM_KEY)
    gw.reset_registry()
    yield
    gw.reset_registry()


async def _new_project(client: AsyncClient, title: str = "偏好测试") -> dict[str, Any]:
    resp = await client.post("/api/v1/projects", json={"title": title})
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


def _url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/model-preference"


# ------------------------------------------------------------------ 校验


async def test_unknown_model_is_rejected(alice: AsyncClient) -> None:
    """存一个目录里没有的 model_id 必须失败。

    放行的后果不是报错而是**静默失效**：Gateway 找不到匹配的路由就按默认
    优先级跑，设置页上却一直显示"已选高质档"。没有任何人会发现。
    """
    project = await _new_project(alice)
    resp = await alice.patch(
        _url(project["id"]),
        json={"capability": CAPABILITY, "model_id": "gpt-does-not-exist"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "provider.params.invalid"
    assert DEFAULT_MODEL in body["error"]["detail"]["allowed"]


async def test_capability_without_provider_is_rejected(alice: AsyncClient) -> None:
    """还没接入任何 Provider 的能力（视频）不能存偏好。

    界面上它本来就是"暂未接入"，没有下拉框；这条挡的是绕过界面直接打接口。
    """
    project = await _new_project(alice)
    resp = await alice.patch(
        _url(project["id"]),
        json={"capability": "image_to_video", "model_id": "whatever-1.0"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "provider.params.invalid"


async def test_cross_tenant_is_404(alice: AsyncClient, bob: AsyncClient) -> None:
    """别的租户的项目一律 404，不是 403——403 会确认这个 id 存在。"""
    project = await _new_project(alice)
    resp = await bob.patch(
        _url(project["id"]), json={"capability": CAPABILITY, "model_id": OTHER_MODEL}
    )
    assert resp.status_code == 404


# ------------------------------------------------------------------ 落库与合并


async def test_preference_is_merged_not_replaced(alice: AsyncClient) -> None:
    """改图片模型不该把之前存的文字模型偏好冲掉。

    前端只会传它正在改的那一个能力。整份覆盖的话，两个设置项互相清空，
    而且用户改完第二项才会发现第一项没了。
    """
    project = await _new_project(alice)

    first = await alice.patch(
        _url(project["id"]), json={"capability": CAPABILITY, "model_id": OTHER_MODEL}
    )
    assert first.status_code == 200
    assert first.json()["model_preference"] == {CAPABILITY: OTHER_MODEL}

    second = await alice.patch(
        _url(project["id"]),
        json={"capability": "image_generation", "model_id": "wan2.2-t2i-plus"},
    )
    assert second.status_code == 200
    assert second.json()["model_preference"] == {
        CAPABILITY: OTHER_MODEL,
        "image_generation": "wan2.2-t2i-plus",
    }

    # 刷新页面还在——真相在库里，不是那一次响应
    again = await alice.get(f"/api/v1/projects/{project['id']}")
    assert again.json()["model_preference"] == {
        CAPABILITY: OTHER_MODEL,
        "image_generation": "wan2.2-t2i-plus",
    }


async def test_null_clears_only_that_capability(alice: AsyncClient) -> None:
    project = await _new_project(alice)
    await alice.patch(_url(project["id"]), json={"capability": CAPABILITY, "model_id": OTHER_MODEL})
    await alice.patch(
        _url(project["id"]),
        json={"capability": "image_generation", "model_id": "wan2.2-t2i-plus"},
    )

    cleared = await alice.patch(
        _url(project["id"]), json={"capability": CAPABILITY, "model_id": None}
    )
    assert cleared.status_code == 200
    assert cleared.json()["model_preference"] == {"image_generation": "wan2.2-t2i-plus"}


async def test_legacy_project_reports_empty_preference(alice: AsyncClient) -> None:
    """没设过偏好的项目给空字典，不是 null——让前端每次写 `?? {}` 是把
    后端的历史包袱转嫁出去。"""
    project = await _new_project(alice)
    assert project["model_preference"] == {}


# ------------------------------------------------------------------ 真的传到了适配器


async def test_preference_reaches_the_adapter(alice: AsyncClient, fake_adapter: Any) -> None:
    """存下的偏好真的决定了这次调用用哪个模型。**这条是整个接线的验收点。**"""
    project = await _new_project(alice)
    org_id = uuid.UUID((await alice.get("/api/v1/auth/me")).json()["org_id"])
    project_id = uuid.UUID(project["id"])
    await breaker.reset(REAL_PROVIDER)

    # 先确认不设偏好时走的是目录里优先级最高的那个
    await gw.generate_text(TextRequest(system="s", user="u"), org_id=org_id, project_id=project_id)
    assert RecordingProvider.calls == [DEFAULT_MODEL]

    resp = await alice.patch(
        _url(project["id"]), json={"capability": CAPABILITY, "model_id": OTHER_MODEL}
    )
    assert resp.status_code == 200

    await gw.generate_text(TextRequest(system="s", user="u"), org_id=org_id, project_id=project_id)
    assert RecordingProvider.calls == [DEFAULT_MODEL, OTHER_MODEL]


async def test_no_reasoning_gate_survives_the_real_lookup(
    alice: AsyncClient, fake_adapter: Any
) -> None:
    """钉住推理模型之后，分类/结构化抽取这类环节仍然拿到非推理模型。

    ADR-024 硬约束 2。这类失败**不会报错**，只会返回空内容，
    所以必须有一条用例把它钉死。
    """
    project = await _new_project(alice)
    org_id = uuid.UUID((await alice.get("/api/v1/auth/me")).json()["org_id"])
    await alice.patch(_url(project["id"]), json={"capability": CAPABILITY, "model_id": OTHER_MODEL})
    await breaker.reset(REAL_PROVIDER)

    await gw.generate_text(
        TextRequest(system="s", user="u"),
        org_id=org_id,
        project_id=uuid.UUID(project["id"]),
        allow_reasoning=False,
    )
    assert RecordingProvider.calls == [DEFAULT_MODEL]


# ------------------------------------------------------------------ 目录接口


async def test_catalog_lists_real_models_and_admits_video_is_missing(alice: AsyncClient) -> None:
    """目录接口要既给出真实可选项，也如实说明哪些能力还没接。

    "没接"必须由后端说出来。让前端自己判断"哪些算视频、视频在哪个里程碑"，
    等于把一句迟早会过期的话冻进前端文案里。
    """
    resp = await alice.get("/api/v1/model-catalog")
    assert resp.status_code == 200
    items = {i["capability"]: i for i in resp.json()["items"]}

    text = items[CAPABILITY]
    assert text["available"] is True
    assert [m["model_id"] for m in text["models"]] == [DEFAULT_MODEL, OTHER_MODEL]
    assert text["default_model_id"] == DEFAULT_MODEL
    assert text["provider_label"] == "DeepSeek"
    # 档位说明只讲模型定位，绝不讲价格——上游一调价，冻在代码里的
    # "更便宜"就变成了谎话
    blob = " ".join(m["label"] + m["note"] for m in text["models"])
    for forbidden in ("元", "便宜", "省钱", "免费", "价格", "¥", "$"):
        assert forbidden not in blob

    image = items["image_generation"]
    assert image["available"] is True
    assert [m["model_id"] for m in image["models"]] == ["wan2.2-t2i-flash", "wan2.2-t2i-plus"]

    video = items["image_to_video"]
    assert video["available"] is False
    assert video["models"] == []
    assert video["default_model_id"] is None
    assert video["unavailable_reason"]


async def test_catalog_requires_auth(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/model-catalog")).status_code == 401
