"""AI Gateway：按能力解析 Provider，带 failover 与熔断。

三层分离（05_ModelRuntimeSpec.md §5、ADR-002）：
    Capability  要什么能力       —— 业务层只说这个
    Provider    哪家提供         —— Gateway 决定
    Runtime     在哪执行         —— M4 引入本地/远程节点

业务代码永远不该出现 `deepseek.generate(...)` 这样的调用
（02_Architecture.md §4 的反面例子）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from typing import Any

from adapters.providers.base import (
    ImageProvider,
    ImageRequest,
    ImageResult,
    TextProvider,
    TextRequest,
    TextResponse,
)
from adapters.providers.dashscope import DashScopeImageProvider
from adapters.providers.deepseek import DeepSeekProvider
from apps.api.core.config import get_settings
from apps.api.core.errors import ERRORS, AppError
from apps.api.core.logging import get_logger
from apps.api.modules.gateway import breaker

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Route:
    """一条能力路由：某个 Provider + 模型 + 优先级。"""

    provider_id: str
    model_id: str
    priority: int  # 数字大的先用
    factory: Any


@dataclass
class Registry:
    routes: dict[str, list[Route]] = field(default_factory=dict)

    def add(self, capability: str, route: Route) -> None:
        self.routes.setdefault(capability, []).append(route)
        self.routes[capability].sort(key=lambda r: -r.priority)

    def for_capability(self, capability: str) -> list[Route]:
        return self.routes.get(capability, [])


_registry: Registry | None = None


def registry() -> Registry:
    global _registry
    if _registry is None:
        _registry = _build()
    return _registry


def reset_registry() -> None:
    global _registry
    _registry = None


def _build() -> Registry:
    """构建能力路由表。

    factory 一律用 `partial` 绑定，**不要用 lambda**。
    lambda 捕获的是变量而不是值，在这个函数里共用 `key` 变量名时，
    等 factory 真正被调用，`key` 早已指向另一家的凭据了——
    表现是 DeepSeek 拿着万相的 Key 去请求，报"api key invalid"。
    """
    s = get_settings()
    reg = Registry()

    deepseek_key = s.deepseek_api_key.get_secret_value()
    if deepseek_key:
        # deepseek-chat 优先级更高：同样的结构化任务它只用 11 个
        # completion token，V4 要 60 个（含推理）。Router 分类、
        # schema 填充这类活儿不需要推理能力。
        for model, priority in (("deepseek-chat", 100), ("deepseek-v4-flash", 80)):
            reg.add(
                "text_generation",
                Route(
                    "provider.deepseek",
                    model,
                    priority=priority,
                    factory=partial(DeepSeekProvider, api_key=deepseek_key, model_id=model),
                ),
            )

    dashscope_key = s.dashscope_api_key.get_secret_value()
    if dashscope_key:
        for model, priority in (("wan2.2-t2i-flash", 100), ("wan2.2-t2i-plus", 60)):
            reg.add(
                "image_generation",
                Route(
                    "provider.dashscope",
                    model,
                    priority=priority,
                    factory=partial(DashScopeImageProvider, api_key=dashscope_key, model_id=model),
                ),
            )

    return reg


@dataclass(frozen=True, slots=True)
class Attempt:
    provider_id: str
    model_id: str
    error_code: str


async def _candidates(capability: str) -> list[Route]:
    routes = registry().for_capability(capability)
    if not routes:
        raise AppError(
            "provider.unavailable",
            message=f"没有注册任何提供 {capability} 的 Provider（是否缺少 API Key？）",
        )

    healthy: list[Route] = []
    degraded: list[Route] = []
    for route in routes:
        if not await breaker.allows(route.provider_id):
            continue
        snap = await breaker.snapshot(route.provider_id)
        (healthy if snap.state is breaker.State.HEALTHY else degraded).append(route)

    # 降级中的排到后面，但不完全排除——总得有机会证明自己恢复了
    return healthy + degraded or routes[:1]


async def generate_text(request: TextRequest) -> TextResponse:
    return await _call("text_generation", "generate_text", request)  # type: ignore[return-value]


async def generate_image(request: ImageRequest) -> ImageResult:
    return await _call("image_generation", "generate_image", request)  # type: ignore[return-value]


async def _call(capability: str, method: str, request: object) -> object:
    """按优先级依次尝试，可 failover 的错误才继续换下一家。

    不是所有失败都该换 Provider：参数错误换一家还是错，
    内容被拒换一家可能还是被拒且又花一次钱。
    该不该换由错误目录决定（21_ErrorTaxonomy.md §2 的 failover 列）。
    """
    attempts: list[Attempt] = []
    last: AppError | None = None

    for route in await _candidates(capability):
        provider: TextProvider | ImageProvider = route.factory()
        try:
            result = await getattr(provider, method)(request)
        except AppError as exc:
            await breaker.record_failure(route.provider_id)
            attempts.append(Attempt(route.provider_id, route.model_id, exc.code))
            last = exc
            spec = ERRORS.get(exc.code)
            if spec is None or not spec.failover:
                log.warning(
                    "gateway.no_failover",
                    capability=capability,
                    provider=route.provider_id,
                    code=exc.code,
                )
                raise
            log.warning(
                "gateway.failover",
                capability=capability,
                from_provider=route.provider_id,
                code=exc.code,
            )
            continue

        await breaker.record_success(route.provider_id)
        if attempts:
            log.info(
                "gateway.recovered_via_failover",
                capability=capability,
                used=route.provider_id,
                failed=[a.provider_id for a in attempts],
            )
        return result

    raise last or AppError("provider.unavailable", message=f"{capability} 的所有 Provider 都不可用")
