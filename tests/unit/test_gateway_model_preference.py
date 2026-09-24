"""项目级模型偏好如何影响路由顺序（ADR-024）。

这一份只测**决策**：偏好把哪条路由排到前面、匹配不上怎么办、
排到前面之后容错还在不在。查库那一环换成假件——"偏好怎么影响顺序"
不该只能靠一整套数据库才验证得了，理由与 `_load_org_key` 单开一个函数一样。

链路另一头（真的走 HTTP 存偏好、真的落 JSONB、真的传到适配器手里）
在 `tests/integration/test_model_preference.py`。只测一头发现不了两头对不上。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from adapters.providers.base import TextRequest, TextResponse
from apps.api.core.errors import AppError
from apps.api.modules.gateway import breaker
from apps.api.modules.gateway import service as gw

ORG = uuid.uuid4()
PROJECT = uuid.uuid4()


class FakeProvider:
    """一个模型一个实例，`calls` 记录它被真的调用过几次。"""

    def __init__(self, *, provider_id: str, model_id: str, fail_with: str | None = None) -> None:
        self.provider_id = provider_id
        self.model_id = model_id
        self._fail_with = fail_with
        self.calls = 0

    async def generate_text(self, request: TextRequest) -> TextResponse:
        del request
        self.calls += 1
        if self._fail_with:
            raise AppError(self._fail_with, message="fake failure")
        return TextResponse(text='{"ok": true}', tokens_in=1, tokens_out=1, model_id=self.model_id)


def _registry_with(*providers: FakeProvider) -> gw.Registry:
    """按传入顺序赋予递减优先级，第一个就是"不带偏好时先试的那个"。"""
    reg = gw.Registry()
    for i, p in enumerate(providers):
        reg.add(
            "text_generation",
            gw.Route(p.provider_id, p.model_id, priority=100 - i, factory=lambda p=p: p),  # type: ignore[misc]
        )
    return reg


def _prefer(monkeypatch: pytest.MonkeyPatch, model_id: str | None) -> None:
    """替掉查库那一环。真实的那一环在集成测试里。"""

    async def _fake(*, org_id: uuid.UUID, project_id: uuid.UUID, capability: str) -> str | None:
        del org_id, project_id, capability
        return model_id

    monkeypatch.setattr(gw, "_load_model_preference", _fake)


@pytest.fixture(autouse=True)
def _clean_registry() -> Any:
    gw.reset_registry()
    yield
    gw.reset_registry()


async def _reset(*providers: FakeProvider) -> None:
    for p in providers:
        await breaker.reset(p.provider_id)


# ------------------------------------------------------------------ 重排


async def test_preferred_model_goes_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """选了低优先级的那个模型，就该真的用它，而不是"默认那个更划算所以还是它"。"""
    fast = FakeProvider(provider_id="p.deepseek", model_id="chat")
    deep = FakeProvider(provider_id="p.deepseek", model_id="v4-flash")
    gw._registry = _registry_with(fast, deep)
    await _reset(fast, deep)
    _prefer(monkeypatch, "v4-flash")

    resp = await gw.generate_text(TextRequest(system="s", user="u"), org_id=ORG, project_id=PROJECT)
    assert resp.model_id == "v4-flash"
    assert fast.calls == 0


async def test_no_preference_keeps_default_order(monkeypatch: pytest.MonkeyPatch) -> None:
    fast = FakeProvider(provider_id="p.deepseek", model_id="chat")
    deep = FakeProvider(provider_id="p.deepseek", model_id="v4-flash")
    gw._registry = _registry_with(fast, deep)
    await _reset(fast, deep)
    _prefer(monkeypatch, None)

    resp = await gw.generate_text(TextRequest(system="s", user="u"), org_id=ORG, project_id=PROJECT)
    assert resp.model_id == "chat"


async def test_project_id_absent_never_queries_preference(monkeypatch: pytest.MonkeyPatch) -> None:
    """不挂项目的调用（脚本、资产库里的独立档案）不该去查一张不存在的偏好。"""
    called = False

    async def _boom(**_kw: Any) -> str | None:
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(gw, "_load_model_preference", _boom)
    fast = FakeProvider(provider_id="p.deepseek", model_id="chat")
    gw._registry = _registry_with(fast)
    await _reset(fast)

    await gw.generate_text(TextRequest(system="s", user="u"), org_id=ORG)
    assert called is False


async def test_stale_preference_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """偏好指向一个目录里已经没有的模型时，按默认优先级跑完，不报错。

    模型下线、目录改绑，都会留下这种陈旧偏好。为它把整个能力停掉，
    等于一次上游改动就让所有存过偏好的项目集体失效。
    """
    fast = FakeProvider(provider_id="p.deepseek", model_id="chat")
    deep = FakeProvider(provider_id="p.deepseek", model_id="v4-flash")
    gw._registry = _registry_with(fast, deep)
    await _reset(fast, deep)
    _prefer(monkeypatch, "model-that-was-retired")

    resp = await gw.generate_text(TextRequest(system="s", user="u"), org_id=ORG, project_id=PROJECT)
    assert resp.model_id == "chat"


async def test_preference_does_not_leak_into_process_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重排只能作用在这一次调用的副本上。

    `registry()` 是进程级单例。就地排序的话，一个项目选了高质档，
    之后**所有租户**的调用都会跟着走高质档——而且没有任何人会发现。
    """
    fast = FakeProvider(provider_id="p.deepseek", model_id="chat")
    deep = FakeProvider(provider_id="p.deepseek", model_id="v4-flash")
    gw._registry = _registry_with(fast, deep)
    await _reset(fast, deep)

    _prefer(monkeypatch, "v4-flash")
    await gw.generate_text(TextRequest(system="s", user="u"), org_id=ORG, project_id=PROJECT)

    assert [r.model_id for r in gw.registry().for_capability("text_generation")] == [
        "chat",
        "v4-flash",
    ]


# ------------------------------------------------------------------ 容错没被破坏


async def test_preferred_model_still_fails_over(monkeypatch: pytest.MonkeyPatch) -> None:
    """**这条是本次改动最重要的回归用例。**

    用户在下拉框里选了一个模型，表达的是"优先用它"，不是"只准用它，
    坏了就整条链路报错"。何况此刻 Credits 已经预扣过了——让一次本可以
    自动绕过的故障把它烧掉，用户是白付钱。
    """
    fast = FakeProvider(provider_id="p.deepseek", model_id="chat")
    deep = FakeProvider(
        provider_id="p.deepseek", model_id="v4-flash", fail_with="provider.unavailable"
    )
    gw._registry = _registry_with(fast, deep)
    await _reset(fast, deep)
    _prefer(monkeypatch, "v4-flash")

    resp = await gw.generate_text(TextRequest(system="s", user="u"), org_id=ORG, project_id=PROJECT)
    # 先试了用户选的那个，它挂了，再落回默认那个——两件事都发生了
    assert deep.calls == 1
    assert resp.model_id == "chat"


async def test_preferred_provider_breaker_open_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    """用户选的模型所在的 Provider 正在熔断时，自动落到同能力的下一家。

    熔断是按 Provider 记的（`breaker.scope`），所以这条只在跨 Provider 时
    才成立——同一家的两个模型共用一个熔断器。同能力多 Provider 是
    目录迟早会到的形态，这条用例把它先钉住。
    """
    chosen = FakeProvider(provider_id="p.chosen", model_id="chosen-model")
    other = FakeProvider(provider_id="p.other", model_id="other-model")
    gw._registry = _registry_with(chosen, other)
    await _reset(chosen, other)
    _prefer(monkeypatch, "chosen-model")

    # 把用户选中的那家打到熔断打开
    for _ in range(20):
        await breaker.record_failure(breaker.scope("p.chosen"))
    assert not await breaker.allows(breaker.scope("p.chosen"))

    try:
        resp = await gw.generate_text(
            TextRequest(system="s", user="u"), org_id=ORG, project_id=PROJECT
        )
        assert resp.model_id == "other-model"
        assert chosen.calls == 0
    finally:
        await breaker.reset("p.chosen")


# ------------------------------------------------------------------ 推理模型闸门


async def test_reasoning_preference_dropped_for_no_reasoning_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-024 硬约束 2：分类/结构化抽取环节不许钉推理模型。

    `deepseek-v4-*` 的思考 token 计入输出预算，用在这类环节上会返回
    **空内容且不报错**——这个仓库踩过两次，测试不会因此变红，
    所以只能在这里把它拦住。
    """
    fast = FakeProvider(provider_id="p.deepseek", model_id="deepseek-chat")
    deep = FakeProvider(provider_id="p.deepseek", model_id="deepseek-v4-flash")
    gw._registry = _registry_with(fast, deep)
    await _reset(fast, deep)
    _prefer(monkeypatch, "deepseek-v4-flash")

    resp = await gw.generate_text(
        TextRequest(system="s", user="u"),
        org_id=ORG,
        project_id=PROJECT,
        allow_reasoning=False,
    )
    assert resp.model_id == "deepseek-chat"
    assert deep.calls == 0


async def test_non_reasoning_preference_survives_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """闸门只拦推理模型，不该顺手把所有偏好一起丢掉。

    `deepseek-chat` 是目录里真有的模型，偏好会被反查到它所属的
    `provider.deepseek` 并钉住那一家——假件的 provider_id 必须与目录一致，
    否则钉住之后路由表里一条都不剩。
    """
    fast = FakeProvider(provider_id="provider.deepseek", model_id="deepseek-chat")
    other = FakeProvider(provider_id="provider.deepseek", model_id="deepseek-lite")
    gw._registry = _registry_with(other, fast)
    await _reset(fast, other)
    _prefer(monkeypatch, "deepseek-chat")

    resp = await gw.generate_text(
        TextRequest(system="s", user="u"),
        org_id=ORG,
        project_id=PROJECT,
        allow_reasoning=False,
    )
    assert resp.model_id == "deepseek-chat"
