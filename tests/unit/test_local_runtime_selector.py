"""llm.py 的逐请求路由。

三条不能破的：

1. **试点关着时，整条链路与接入之前逐字相同。** `ENV=test` 拿到的仍然
   必须是 `MockLLM` 本体（`test_no_live_provider_in_tests.py` 在断言这件事）。
2. **开着时按请求分流**：白名单内走本地，白名单外照常走原实现。
3. **选中本地之后失败就是失败**，不许退回 Gateway（那要花钱）
   也不许退回 Mock（那会把假数据写进档案）。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest

from apps.api.core.config import Settings
from apps.api.core.errors import AppError
from apps.api.modules.agent import llm
from apps.api.modules.local_runtime import service as local_runtime

ORG = uuid.UUID("11111111-1111-1111-1111-111111111111")
PROJECT = uuid.UUID("33333333-3333-3333-3333-333333333333")
OTHER_PROJECT = uuid.UUID("44444444-4444-4444-4444-444444444444")


def _pilot_settings() -> Settings:
    # env=test：这样 get_provider() 里被包住的那一层必然是 MockLLM，
    # 断言才不会因为运行环境里有没有 DEEPSEEK_API_KEY 而漂移。
    kwargs: dict[str, object] = {
        "env": "test",
        "local_cli_enabled": True,
        "local_cli_org_id": ORG,
        "local_cli_project_ids": [PROJECT],
        "local_cli_provider": "codex",
        "local_cli_token": "z" * 40,
    }
    return Settings(**kwargs)  # type: ignore[arg-type]


def _request(project_id: uuid.UUID | None) -> llm.LLMRequest:
    return llm.LLMRequest(
        system="s",
        user="把这篇小说做成 5 分钟悬疑漫剧",
        schema_name="StoryOutline",
        max_output_tokens=1024,
        org_id=ORG,
        project_id=project_id,
    )


@pytest.fixture(autouse=True)
def _reset_provider() -> Iterator[None]:
    # 前后都要清：`_provider` 是模块级单例，留着会污染同一轮里的别的用例。
    llm.set_provider(None)
    yield
    llm.set_provider(None)


class TestSelection:
    def test_disabled_pilot_leaves_get_provider_untouched(self) -> None:
        """关着时连一层壳都不包——包了就会让 `isinstance(p, MockLLM)` 变假。"""
        provider = llm.get_provider()
        assert isinstance(provider, llm.MockLLM)
        assert not isinstance(provider, llm.RoutingLLM)

    def test_enabled_pilot_wraps_the_existing_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from apps.api.core import config as config_module

        monkeypatch.setattr(config_module, "get_settings", _pilot_settings)
        provider = llm.get_provider()
        assert isinstance(provider, llm.RoutingLLM)
        # 外面那层换了，里面那层还必须是 ENV=test 该有的 Mock。
        assert isinstance(provider.fallback, llm.MockLLM)


class TestRouting:
    async def test_whitelisted_project_goes_local(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(local_runtime, "get_settings", _pilot_settings)
        calls: list[str] = []

        async def _fake_complete(**kwargs: object) -> local_runtime.LocalCompletion:
            calls.append(str(kwargs["schema_name"]))
            return local_runtime.LocalCompletion(
                text='{"ok": true}', model_id="local-cli.codex", tokens_in=0, tokens_out=0
            )

        monkeypatch.setattr(local_runtime, "complete_text", _fake_complete)

        routing = llm.RoutingLLM(llm.MockLLM())
        response = await routing.complete(_request(PROJECT))

        assert calls == ["StoryOutline"]
        assert response.text == '{"ok": true}'
        # 如实标注执行者：agent_runs.model_id 是后面成本复盘的唯一依据。
        assert response.model_id == "local-cli.codex"

    async def test_other_project_keeps_the_old_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(local_runtime, "get_settings", _pilot_settings)

        async def _must_not_be_called(**kwargs: object) -> local_runtime.LocalCompletion:
            raise AssertionError("白名单外的项目不该走本地 CLI")

        monkeypatch.setattr(local_runtime, "complete_text", _must_not_be_called)

        routing = llm.RoutingLLM(llm.MockLLM())
        response = await routing.complete(_request(OTHER_PROJECT))
        assert response.model_id == llm.MockLLM.model_id

    async def test_local_failure_never_falls_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """选中本地之后，失败必须以失败告终。

        退回 Gateway 是拿用户的钱补一次他以为在用订阅额度的调用；
        退回 Mock 更糟——假档案会被当成真产出写进库，而且不会有任何报错。
        """
        monkeypatch.setattr(local_runtime, "get_settings", _pilot_settings)

        async def _boom(**kwargs: object) -> local_runtime.LocalCompletion:
            raise AppError("provider.unavailable", message="local runner is not connected")

        monkeypatch.setattr(local_runtime, "complete_text", _boom)

        fallback_calls: list[int] = []

        class _Tripwire:
            async def complete(self, request: llm.LLMRequest) -> llm.LLMResponse:
                fallback_calls.append(1)
                raise AssertionError("本地失败后不许回落")

        routing = llm.RoutingLLM(_Tripwire())
        with pytest.raises(AppError) as exc:
            await routing.complete(_request(PROJECT))
        assert exc.value.code == "provider.unavailable"
        assert fallback_calls == []
