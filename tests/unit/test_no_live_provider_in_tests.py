"""测试环境绝不能打真实 Provider。

这不是洁癖。测试环境有 Key 时，如果默认走真实模型：
- 每跑一遍测试都在真花钱，CI 频率越高烧得越多
- 网络抖动、限流、上游变更都会变成"测试挂了"
- 输出不确定，断言只能写得极松，等于没测

安全默认必须写在生产代码里，而不是靠 conftest 记得去设——
漏一次就开始烧钱，且不会有任何报错提示你。
"""

from __future__ import annotations

from apps.api.core.config import get_settings
from apps.api.modules.agent import llm


def test_env_is_test() -> None:
    assert get_settings().env == "test", "测试必须在 ENV=test 下运行"


def test_provider_is_mock_even_when_keys_present() -> None:
    """有 Key 也必须用 Mock。"""
    llm.set_provider(None)  # 清缓存，走真实选择逻辑
    provider = llm.get_provider()
    assert isinstance(provider, llm.MockLLM), (
        f"测试环境拿到了 {type(provider).__name__}，会产生真实调用与费用"
    )


def test_mock_is_deterministic() -> None:
    """同样输入必须得到同样输出。

    不确定的 Mock 会让测试时红时绿，比没有 Mock 更糟。
    """
    import asyncio

    async def once() -> str:
        return (
            await llm.MockLLM().complete(
                llm.LLMRequest(
                    system="s",
                    user="把这篇小说做成 5 分钟悬疑漫剧",
                    schema_name="StoryOutline",
                    max_output_tokens=1024,
                )
            )
        ).text

    a, b = asyncio.run(once()), asyncio.run(once())
    assert a == b
