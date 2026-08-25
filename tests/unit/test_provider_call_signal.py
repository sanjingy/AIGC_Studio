"""调用信号：每次上游调用都记下"用的是谁的 Key"（ADR-027）。

这个信号存在的理由是**验证手段**：BYOK 接线对不对，光看计费数字看不出来
——计费查的是库里有没有那一行，调用用的是解出来的那把 Key，两者完全可能
背离，而背离的方向恰好是平台掏钱。信号打在适配器里，也就是真正把 Key
放进 Authorization 头的那一层，中间任何一次传参错位它都能看见。

同时它自己是一条红线：**只允许出现来源枚举，不允许出现任何密钥材料。**
"""

from __future__ import annotations

from typing import Any

import pytest

from adapters.providers import base
from adapters.providers.dashscope import DashScopeImageProvider
from adapters.providers.deepseek import DeepSeekProvider

SECRET = "sk-super-secret-key-abcdef123456"


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr(base, "log", rec)
    return rec


def test_org_key_is_signalled_as_org(recorder: _Recorder) -> None:
    DeepSeekProvider(api_key=SECRET, key_source=base.KeySource.ORG)._signal_call()

    event, payload = recorder.events[0]
    assert event == "provider.call"
    assert payload["key_source"] == "org"
    assert payload["provider_id"] == "provider.deepseek"


def test_default_is_platform(recorder: _Recorder) -> None:
    """漏传只会把自有 Key 的调用记成平台调用；反过来才是危险的默认。"""
    DashScopeImageProvider(api_key=SECRET)._signal_call()

    assert recorder.events[0][1]["key_source"] == "platform"


def test_signal_never_carries_key_material(recorder: _Recorder) -> None:
    for provider in (
        DeepSeekProvider(api_key=SECRET, key_source=base.KeySource.ORG),
        DashScopeImageProvider(api_key=SECRET, key_source=base.KeySource.ORG),
    ):
        provider._signal_call()

    blob = repr(recorder.events)
    assert SECRET not in blob
    # 片段同样不行：露出一半的 Key 仍然是泄露
    assert SECRET[:12] not in blob
    assert SECRET[8:20] not in blob
