"""无 Key 时的出图回退（FR-CONS-011）。

三条选择规则各一个用例，外加占位图的稳定性。规则本身与
`agent/llm.py::get_provider` 一致，理由见 `gateway/mock_image.py` 的模块
文档：安全默认必须写在生产代码里，靠 conftest 去设的话漏一次就开始烧钱。

**改环境变量一律用赋值，且之后 `get_settings.cache_clear()`**——
`setdefault` 在 compose 已经注入该变量时不生效，这个仓库因此栽过两次。
`monkeypatch.setenv` 就是赋值。
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

import pytest

from adapters.providers.base import ImageRequest
from apps.api.core import config
from apps.api.modules.gateway import mock_image
from apps.api.modules.gateway import service as gw

CAP = mock_image.CAPABILITY


@pytest.fixture(autouse=True)
def _restore_settings() -> Any:
    """每个用例后把 Settings 缓存和路由表还原。

    两者都是进程级单例，用例里改了环境变量却不清缓存，
    后面的用例会读到上一轮的值。
    """
    yield
    config.get_settings.cache_clear()
    gw.reset_registry()


def _with_env(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    config.get_settings.cache_clear()
    gw.reset_registry()


# ---------------------------------------------------------------- 三条规则


def test_rule_1_test_env_always_mocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """规则 1：`ENV=test` 有 Key 也用 Mock。

    这条不能靠 conftest 设。测试环境有 Key 时漏设一次，就是每跑一遍
    测试都在真花钱，还会把网络抖动和限流带进 CI。
    """
    _with_env(monkeypatch, ENV="test", DASHSCOPE_API_KEY="sk-real-key-XXXX")
    assert mock_image.forced(CAP) is True


def test_rule_2_key_present_uses_real_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """规则 2：有平台 Key 就走真实 Gateway，两个谓词都不该点亮。"""
    _with_env(monkeypatch, ENV="local", DASHSCOPE_API_KEY="sk-real-key-XXXX")
    assert mock_image.forced(CAP) is False
    assert mock_image.fallback(CAP) is False

    routes = gw.registry().for_capability(CAP)
    assert routes, "有 Key 时必须有真实路由"
    assert all(r.provider_id != mock_image.PROVIDER_ID for r in routes)


def test_rule_3_no_key_no_longer_falls_back_to_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """规则 3 **已被撤销**：缺 Key 不再兜底成占位图。

    撤销的理由记在 `mock_image.fallback` 的 docstring 里：部署出去但没配 Key
    的环境，用户点"生成"拿回一张写着 MOCK IMAGE 的占位图，任务 `succeeded`、
    Credits 照扣，而界面上没有任何地方说过这张图是假的——他会拿着它去对画风。
    现在缺 Key 一律 `provider.unavailable`，如实报错。

    这条用例断言的是**撤销本身**：谓词恒 False，且 `ENV=local` 下没有任何
    Mock 路由被注册进来。哪天有人把兜底加回去，这里会红。
    """
    _with_env(monkeypatch, ENV="local", DASHSCOPE_API_KEY="")
    assert mock_image.forced(CAP) is False
    assert mock_image.fallback(CAP) is False

    routes = gw.registry().for_capability(CAP)
    assert all(r.provider_id != mock_image.PROVIDER_ID for r in routes), (
        "缺 Key 时不该再有 Mock 路由——那正是被撤销的兜底"
    )


def test_text_generation_is_never_mocked_here(monkeypatch: pytest.MonkeyPatch) -> None:
    """这一层只管出图。文本的 Mock 在 `agent/llm.py`，两处不能互相接管。"""
    _with_env(monkeypatch, ENV="test", DEEPSEEK_API_KEY="")
    assert mock_image.forced("text_generation") is False
    assert mock_image.fallback("text_generation") is False


# ---------------------------------------------------------------- 解析出口


async def test_resolution_prefers_mock_over_byok_in_test_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """规则 1 必须压过 BYOK。

    否则一个存了自有 DashScope Key 的测试租户，跑到出图照样打真上游——
    "测试不花钱"这条保证就有一个洞。
    """
    _with_env(monkeypatch, ENV="test", DASHSCOPE_API_KEY="sk-real-key-XXXX")

    called = False

    async def _boom(**_kw: Any) -> None:
        nonlocal called
        called = True
        raise AssertionError("测试环境不该去查 BYOK 凭据")

    monkeypatch.setattr(gw, "_load_org_key", _boom)

    import uuid

    resolution = await gw._resolve(CAP, org_id=uuid.uuid4())
    assert [r.provider_id for r in resolution.routes] == [mock_image.PROVIDER_ID]
    assert called is False


async def test_resolution_keeps_byok_when_platform_has_no_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """规则 3 必须让位于 BYOK。

    平台没 Key、用户自带 Key，该用他的那把——回退到 Mock 等于把
    "我配了 Key 为什么还是假图"送给用户。
    """
    import uuid

    from apps.api.modules.billing import credentials

    _with_env(monkeypatch, ENV="local", DASHSCOPE_API_KEY="")

    async def _own(**_kw: Any) -> credentials.ResolvedKey:
        return credentials.ResolvedKey(
            provider_id="provider.dashscope", api_key="sk-user-YYYY", capability=CAP
        )

    monkeypatch.setattr(gw, "_load_org_key", _own)

    async def _no_default(**_kw: Any) -> None:
        return None

    monkeypatch.setattr(gw, "_load_org_default", _no_default)

    resolution = await gw._resolve(CAP, org_id=uuid.uuid4())
    assert [r.provider_id for r in resolution.routes] == ["provider.dashscope"] * len(
        resolution.routes
    )
    assert resolution.routes, "BYOK 路由被 Mock 顶掉了"


# ---------------------------------------------------------------- 不进模型目录


def test_mock_is_not_in_the_user_facing_catalog() -> None:
    """Mock 不能出现在模型页面上。

    `catalog.SPECS` 就是 `/freeflow/models` 的数据源。混进去的话用户会在
    下拉框里看见一个假模型，甚至能把项目钉在上面。
    """
    from apps.api.modules.gateway import catalog

    assert all(spec.provider_id != mock_image.PROVIDER_ID for spec in catalog.SPECS)
    assert mock_image.MODEL_ID not in catalog.model_ids(CAP)
    assert mock_image.MODEL_ID not in catalog.MODEL_LABELS


# ---------------------------------------------------------------- 占位图


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_placeholder_is_a_1024_png() -> None:
    import io

    from PIL import Image

    data = mock_image.render_placeholder(_digest("一号镜"), size=(1024, 1024), caption="x")
    image = Image.open(io.BytesIO(data))
    assert image.format == "PNG"
    assert image.size == (1024, 1024)
    assert len(data) > 0


def test_same_prompt_same_image() -> None:
    """同一个提示词必须稳定出同一张图。

    不确定的 Mock 会让测试时红时绿，比没有 Mock 更糟。
    """
    request = ImageRequest(prompt="主角站在铁门外", seed=7)

    async def once() -> bytes:
        digest = mock_image._digest(request, 0)
        return mock_image.render_placeholder(digest, size=(256, 256), caption=digest[:8])

    assert asyncio.run(once()) == asyncio.run(once())


def test_different_prompts_differ_visibly() -> None:
    """不同提示词必须明显不同。

    十个镜头出十张一样的图，光看资产库根本判断不出流程是不是真的
    按镜头走了一遍——那才是这张占位图唯一的用途。
    """
    seen = set()
    for index in range(1, 11):
        request = ImageRequest(prompt=f"第 {index} 镜的画面内容", seed=100 + index)
        digest = mock_image._digest(request, 0)
        seen.add(mock_image.render_placeholder(digest, size=(256, 256), caption=digest[:8]))
    assert len(seen) == 10, "十个镜头出了重复的图"


def test_size_is_honoured_and_bad_size_degrades() -> None:
    """尺寸解析不了就退回默认，不能让无 Key 环境因为一个格式错重新变成不可用。"""
    assert mock_image._parse_size("1024*1024") == (1024, 1024)
    assert mock_image._parse_size("768*1344") == (768, 1344)
    assert mock_image._parse_size("很大") == mock_image.DEFAULT_SIZE
