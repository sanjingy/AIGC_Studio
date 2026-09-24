"""AI Gateway：按能力解析 Provider，带 failover 与熔断。

三层分离（05_ModelRuntimeSpec.md §5、ADR-002）：
    Capability  要什么能力       —— 业务层只说这个
    Provider    哪家提供         —— Gateway 决定
    Runtime     在哪执行         —— M4 引入本地/远程节点

业务代码永远不该出现 `deepseek.generate(...)` 这样的调用
（02_Architecture.md §4 的反面例子）。

**Key 的来源在这里决定（ADR-027）**。平台自己的 Key 走进程级路由表，
进程启动时建一次；某个 org 给某个能力配了自己的 Key 时，这次调用
**只走他那把**，每次请求现查现解密。两条路的差别不止是"用哪把钥匙"：

- 熔断状态分开算。他的 Key 被上游封了不该把用平台档的其他人一起拖下水，
  反过来平台档正在熔断也不该拦住他——两边的账号根本不是同一个。
- 不跨 Provider failover 回平台。他只配了一家，"轮换到另一家"这个选项
  并不存在；而计费此刻已经按 BYOK 折扣算过了（ADR-025），
  这时候拿平台 Key 顶上就是平台掏钱、用户按折扣价付款。

**项目级模型偏好也在这里生效（ADR-024）**。调用带上 `project_id` 时，
Gateway 查 `projects.model_preference[capability]`，把匹配的那条路由
**排到最前**——注意是重排不是过滤：用户选了一个模型不等于他要求关掉容错。
选中的模型熔断了、或者这一次调用失败了，仍然按原来的优先级顺序落到
同能力的下一个模型上。"只准跑这一个，坏了就报错"从来不是用户在下拉框里
选一次时表达的意思。

**选哪家、用谁的 Key 由 `upstreams.decide` 判定**（项目选择 > 组织默认 >
平台目录默认）。它和计费的 BYOK 判断是同一个函数，所以"页面上存的
Provider / 模型 / 计费来源"和"这次真的拿去调的"是同一份结论。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from adapters.providers.base import (
    ImageProvider,
    ImageRequest,
    ImageResult,
    KeySource,
    TextProvider,
    TextRequest,
    TextResponse,
)
from adapters.providers.openai_compat import OpenAICompatTextProvider
from apps.api.core.db import session_scope
from apps.api.core.errors import ERRORS, AppError
from apps.api.core.logging import get_logger
from apps.api.modules.billing import credentials
from apps.api.modules.gateway import breaker, catalog, mock_image, probe, upstreams
from apps.api.modules.project import service as project_service

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Route:
    """一条能力路由：某个 Provider + 模型 + 优先级 + 这次用谁的 Key。"""

    provider_id: str
    model_id: str
    priority: int  # 数字大的先用
    factory: Any
    key_source: KeySource = KeySource.PLATFORM
    # 只有 BYOK 路由有值。它决定熔断记在谁头上，不参与路由选择本身。
    org_id: uuid.UUID | None = None

    @property
    def breaker_scope(self) -> str:
        return breaker.scope(self.provider_id, org_id=self.org_id)


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
    """平台自己那张路由表。进程级单例，只含平台 Key 的路由。

    BYOK 的路由**不进这张表**：它是每个 org 各一份、随时会被删的东西，
    塞进进程级单例既要处理失效，又等于在内存里长期存着一堆明文 Key。
    """
    global _registry
    if _registry is None:
        _registry = _build()
    return _registry


def reset_registry() -> None:
    global _registry
    _registry = None


def _routes_for(
    spec: catalog.ProviderSpec,
    *,
    api_key: str,
    key_source: KeySource,
    org_id: uuid.UUID | None = None,
) -> list[Route]:
    """把目录里的一家 Provider 展开成若干条路由（一个模型一条）。

    factory 一律用 `partial` 绑定，**不要用 lambda**。
    lambda 捕获的是变量而不是值，在循环里共用 `model` / `api_key` 变量名时，
    等 factory 真正被调用，变量早已指向下一轮的值了——
    表现是 DeepSeek 拿着万相的 Key 去请求，报"api key invalid"。
    """
    return [
        Route(
            spec.provider_id,
            model,
            priority=priority,
            factory=partial(spec.adapter, api_key=api_key, model_id=model, key_source=key_source),
            key_source=key_source,
            org_id=org_id,
        )
        for model, priority in spec.models
    ]


def _build() -> Registry:
    """构建平台能力路由表。没配 Key 的 Provider 直接不进表。"""
    reg = Registry()
    for spec in catalog.SPECS:
        key = catalog.platform_key(spec.provider_id)
        if not key:
            continue
        for route in _routes_for(spec, api_key=key, key_source=KeySource.PLATFORM):
            reg.add(spec.capability, route)
    return reg


# 换个模型也改变不了结论的错误。上游对"这个账号能不能用"的判断
# 与模型无关：401 / 欠费在 fast 上是这个答案，在 plus 上还是同一个答案。
# 平台档不适用这条——那里的下一条候选往往是**另一家** Provider、
# 另一个账号，换过去是有意义的。
_ACCOUNT_LEVEL_CODES = frozenset({"provider.account.insufficient"})


@dataclass(frozen=True, slots=True)
class Attempt:
    provider_id: str
    model_id: str
    error_code: str


@dataclass(frozen=True, slots=True)
class Resolution:
    """这次调用要用谁的 Key、可以试哪几条路由。

    `secret` 只有一个用途：把上游回显在错误信息里的密钥材料抹掉
    （上游 401 常把收到的 Key 原样贴回来）。它不进日志、不进异常、
    不出这个模块。
    """

    capability: str
    routes: list[Route]
    key_source: KeySource
    secret: str = ""


async def _load_model_preference(
    *, org_id: uuid.UUID, project_id: uuid.UUID, capability: str
) -> str | None:
    """查这个项目给这个能力钉了哪个模型（ADR-024）。

    单开一个函数的理由同 `_load_org_key`：它是这条链路上第二个需要真实
    数据库的环节，而"偏好怎么影响路由顺序"本身不该只能靠整套 DB 才测得了。
    """
    async with session_scope() as db:
        return await project_service.get_model_preference(
            db, org_id=org_id, project_id=project_id, capability=capability
        )


def _prefer_model(routes: list[Route], model_id: str | None) -> list[Route]:
    """把用户钉的那个模型排到最前，**其余原样保留在后面**。

    重排而不是过滤，是这个函数存在的全部意义。过滤掉其余路由会让
    "我想用高质档"变成"高质档挂了就整条链路报错"——用户在下拉框里
    选一次表达不了这么强的意思，而且这是一条**已经预扣过 Credits** 的
    调用，让它因为一个本可以自动绕过的故障失败，代价是用户白付一次钱。

    匹配不上就整份原样返回：目录改过、模型下线、偏好过期——这些情况下
    按默认优先级继续跑是对的，为一条陈旧的偏好把整个能力停掉不是。
    """
    if not model_id:
        return routes
    chosen = [r for r in routes if r.model_id == model_id]
    if not chosen:
        log.info("gateway.preference_stale", model_id=model_id)
        return routes
    return chosen + [r for r in routes if r.model_id != model_id]


async def _preferred_model(
    *,
    capability: str,
    org_id: uuid.UUID | None,
    project_id: uuid.UUID | None,
    allow_reasoning: bool,
) -> str | None:
    """解析出这次调用该优先用哪个模型。没有偏好返回 None。

    `allow_reasoning=False` 时推理模型的偏好会被丢掉，这是 ADR-024 的
    第二条硬约束：`deepseek-v4-*` 的思考 token 计入输出预算，钉在分类和
    结构化抽取上会**返回空内容且不报错**（已踩过两次）。调用方知道自己
    是什么 role，Gateway 不知道，所以这个判断由调用方给结论、这里执行。

    只拦偏好、不动默认路由：默认优先级本来就把非推理模型排在前面，
    推理模型只有在前一个真的失败之后才会被试到，那是一条罕见的降级路径，
    不在本次改动的范围里；而钉住偏好会让它变成**每一次调用的首选**，
    这才是新引入的风险。
    """
    if org_id is None or project_id is None:
        return None
    model_id = await _load_model_preference(
        org_id=org_id, project_id=project_id, capability=capability
    )
    if model_id and not allow_reasoning and catalog.is_reasoning(model_id):
        log.info(
            "gateway.preference_dropped_no_reasoning",
            capability=capability,
            model_id=model_id,
        )
        return None
    return model_id


async def _load_org_key(
    *, org_id: uuid.UUID, capability: str, provider_id: str
) -> credentials.ResolvedKey | None:
    """查这个 org 有没有给这个能力的**这一家**配自己的 Key。

    单开一个函数是为了给测试一个替换点——它是这条链路上需要真实
    数据库的一环，而路由/熔断/failover 的行为不该只能靠整套 DB 才能测。
    """
    async with session_scope() as db:
        return await credentials.resolve_for_call(
            db, org_id=org_id, capability=capability, provider_id=provider_id
        )


async def _load_org_default(*, org_id: uuid.UUID, capability: str) -> upstreams.DefaultRow | None:
    """组织默认（`org_model_defaults`）。替换点，理由同 `_load_org_key`。"""
    async with session_scope() as db:
        return await upstreams.load_default(db, org_id=org_id, capability=capability)


async def _load_custom_endpoint(*, org_id: uuid.UUID) -> upstreams.ResolvedEndpoint | None:
    """文本自定义端点（`org_text_endpoints`）。替换点，理由同 `_load_org_key`。"""
    async with session_scope() as db:
        return await upstreams.load_endpoint(db, org_id=org_id)


def _mock_resolution(capability: str) -> Resolution:
    """一条临时的 Mock 路由。**不进 `registry()`**。

    路由表是按 `catalog.SPECS` 建的，而 Mock 刻意不在那份目录里
    （目录就是模型页面的数据源）。在解析出口处兜一条，既不污染目录，
    也让下游（熔断、failover、`_attempt`）完全按原样工作。
    """
    return Resolution(
        capability=capability,
        routes=[
            Route(
                mock_image.PROVIDER_ID,
                mock_image.MODEL_ID,
                priority=0,
                factory=mock_image.MockImageProvider,
            )
        ],
        key_source=KeySource.PLATFORM,
    )


def _prefer(
    routes: list[Route], *, provider_id: str | None, model_id: str | None, pinned: bool
) -> list[Route]:
    """按选中的上游与模型排路由。

    `pinned=True`（组织或项目**明确选过**这家）时只留这一家的路由：
    用户选了 A 家，A 家平台 Key 没配或全挂了，不能静默拿 B 家顶上——
    那是"页面上存的"和"真调用的"对不上。同一家内部仍然是重排不是过滤，
    选中的模型坏了照样 failover 到这家的下一个模型（ADR-031 第 7 条）。

    `pinned=False` 是没人选过，按平台目录的默认顺序跑，跨家 failover 照旧。
    """
    if provider_id is not None and pinned:
        routes = [r for r in routes if r.provider_id == provider_id]
    elif provider_id is not None:
        routes = [r for r in routes if r.provider_id == provider_id] + [
            r for r in routes if r.provider_id != provider_id
        ]
    return _prefer_model(routes, model_id)


def _custom_resolution(
    capability: str, endpoint: upstreams.ResolvedEndpoint, org_id: uuid.UUID
) -> Resolution:
    """文本自定义端点的那一条虚拟路由（05_MODEL_GATEWAY.md §5.2）。

    **只在文本能力这一个分支里拼**：`decide()` 对别的能力永远不会给出
    自定义端点，`supports_custom_endpoint` 是写死的能力判断，表里也没有
    capability 列——视频 / TTS 的解析路径碰不到它。
    """
    assert catalog.supports_custom_endpoint(capability)
    route = Route(
        catalog.CUSTOM_TEXT_PROVIDER_ID,
        endpoint.model_id,
        priority=0,
        factory=partial(
            OpenAICompatTextProvider,
            base_url=endpoint.base_url,
            api_key=endpoint.api_key,
            model_id=endpoint.model_id,
            key_source=KeySource.ORG,
        ),
        key_source=KeySource.ORG,
        org_id=org_id,
    )
    log.info(
        "gateway.key_source",
        capability=capability,
        org_id=str(org_id),
        key_source=KeySource.ORG.value,
        provider=catalog.CUSTOM_TEXT_PROVIDER_ID,
    )
    return Resolution(
        capability=capability, routes=[route], key_source=KeySource.ORG, secret=endpoint.api_key
    )


async def _resolve(
    capability: str,
    *,
    org_id: uuid.UUID | None,
    preferred_model_id: str | None = None,
) -> Resolution:
    """决定这次调用用哪家、哪个模型、谁的 Key，以及路由按什么顺序试。

    选哪家由 `upstreams.decide` 给（项目选择 > 组织默认 > 平台目录默认）。
    用谁的 Key：组织默认对这家显式选了计费来源就照它；没选过就按旧规则
    ——这家有自己的 Key 就只用他那把（ADR-027）。

    用他那把时**只**返回他那家的路由，不把平台路由缀在后面。缀上去的话，
    用户的 Key 一失败就会自动落到平台档，用户看不到自己的 Key 坏了，
    平台默默替他付了钱，账面上还是 BYOK 折扣价。

    出图还有 Mock（`mock_image`，FR-CONS-011）。它分两段插在这里而不是合成
    一个判断，因为 `ENV=test` 那一条必须**压过 BYOK**（测试环境里 org 存了
    自己的 Key 也不许打真上游），而"平台没配 Key"那一条必须**让位于 BYOK**
    （平台没 Key、用户自带 Key，该用他的）。
    """
    if mock_image.forced(capability):
        return _mock_resolution(capability)

    default = (
        await _load_org_default(org_id=org_id, capability=capability)
        if org_id is not None
        else None
    )
    decision = upstreams.decide(capability, preference=preferred_model_id, default=default)
    pinned = decision.layer != "platform" and decision.provider_id is not None

    if decision.provider_id == catalog.CUSTOM_TEXT_PROVIDER_ID:
        endpoint = await _load_custom_endpoint(org_id=org_id) if org_id is not None else None
        if endpoint is None or org_id is None:
            raise AppError(
                "provider.byok.rejected",
                message="选中的是文本自定义端点，但它还没有配置或已被删除，请到模型页重新选择",
                detail={"capability": capability, "reason": "custom_endpoint_missing"},
            )
        return _custom_resolution(capability, endpoint, org_id)

    provider_id = decision.provider_id or catalog.provider_of().get(capability)
    use_org_key = decision.key_source is KeySource.ORG
    own: credentials.ResolvedKey | None = None
    if (
        org_id is not None
        and provider_id is not None
        and decision.key_source is not KeySource.PLATFORM
    ):
        own = await _load_org_key(org_id=org_id, capability=capability, provider_id=provider_id)
        if own is None and use_org_key:
            # 组织明确选了"用这家自己的 Key"，Key 却不在了。不拿平台 Key 顶上：
            # 计费已经按 BYOK 折扣算过，顶上去就是平台掏钱。
            raise AppError(
                "provider.byok.rejected",
                message=(
                    f"{capability} 选的是用你自己的 {catalog.provider_label(provider_id)} "
                    "Key 计费，但 Key 已被移除，请到模型页重新配置"
                ),
                detail={"capability": capability, "reason": "key_missing"},
            )

    if own is not None and org_id is not None:
        spec = catalog.spec_for(own.provider_id, capability=capability)
        if spec is None:
            # 存 Key 之后平台把这个能力改绑到别家了。不能拿平台 Key 顶上
            # （理由同上），也不能拿这把 Key 去调另一家的接口。
            raise AppError(
                "provider.byok.rejected",
                message=(
                    f"你为 {capability} 配置的 Provider {own.provider_id} 已不再提供该能力，"
                    "请到设置页重新配置"
                ),
                detail={"capability": capability, "reason": "provider_retired"},
            )
        log.info(
            "gateway.key_source",
            capability=capability,
            org_id=str(org_id),
            key_source=KeySource.ORG.value,
            provider=spec.provider_id,
        )
        return Resolution(
            capability=capability,
            routes=_prefer_model(
                _routes_for(spec, api_key=own.api_key, key_source=KeySource.ORG, org_id=org_id),
                decision.model_id,
            ),
            key_source=KeySource.ORG,
            secret=own.api_key,
        )

    if mock_image.fallback(capability):
        return _mock_resolution(capability)

    # 取副本再重排：`registry()` 是进程级单例，就地排序会让一个项目的
    # 偏好泄漏给所有租户的后续调用。
    return Resolution(
        capability=capability,
        routes=_prefer(
            list(registry().for_capability(capability)),
            provider_id=decision.provider_id,
            model_id=decision.model_id,
            pinned=pinned,
        ),
        key_source=KeySource.PLATFORM,
    )


async def _candidates(resolution: Resolution) -> list[Route]:
    if not resolution.routes:
        # 这里以前几乎走不到：缺 Key 时文本退回 MockLLM、出图退回 mock_image，
        # 用户拿到的是假内容而不是这条错误。两条自动兜底都撤掉之后，
        # "一个模型都没有"如实走到这里，所以文案要能直接给用户看——
        # 说清楚差什么、该去哪儿配，不要求他去读日志。
        raise AppError(
            "provider.unavailable",
            message=(
                f"没有可用的模型来完成「{resolution.capability}」。"
                "请在设置页配置你自己的 Provider Key，或联系管理员为平台配置 Key。"
            ),
            detail={"capability": resolution.capability},
        )

    healthy: list[Route] = []
    degraded: list[Route] = []
    for route in resolution.routes:
        if not await breaker.allows(route.breaker_scope):
            continue
        snap = await breaker.snapshot(route.breaker_scope)
        (healthy if snap.state is breaker.State.HEALTHY else degraded).append(route)

    # 降级中的排到后面，但不完全排除——总得有机会证明自己恢复了
    return healthy + degraded or resolution.routes[:1]


async def generate_text(
    request: TextRequest,
    *,
    org_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    allow_reasoning: bool = True,
) -> TextResponse:
    """文本生成。

    `project_id` 只用来读项目级模型偏好（ADR-024）。漏传的后果是"用户选的
    模型不生效"，串不到别的租户——偏好的读取同时带 org_id。
    `allow_reasoning=False` 由调用方在 role 属于 `no_reasoning_roles` 时给出。
    """
    return await _call(  # type: ignore[return-value]
        "text_generation",
        "generate_text",
        request,
        org_id=org_id,
        project_id=project_id,
        allow_reasoning=allow_reasoning,
    )


async def generate_image(
    request: ImageRequest,
    *,
    org_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
) -> ImageResult:
    """出图。出图模型里没有"推理模型"这回事，所以不带 `allow_reasoning`。"""
    return await _call(  # type: ignore[return-value]
        "image_generation",
        "generate_image",
        request,
        org_id=org_id,
        project_id=project_id,
    )


async def _call(
    capability: str,
    method: str,
    request: object,
    *,
    org_id: uuid.UUID | None,
    project_id: uuid.UUID | None = None,
    allow_reasoning: bool = True,
) -> object:
    """按优先级依次尝试，可 failover 的错误才继续换下一家。

    不是所有失败都该换 Provider：参数错误换一家还是错，
    内容被拒换一家可能还是被拒且又花一次钱。
    该不该换由错误目录决定（21_ErrorTaxonomy.md §2 的 failover 列）。

    BYOK 时候选集里只有那一家的几个模型，所以"换下一家"退化成
    "同一把 Key 换个模型"——同账号同价，换得起；跨到平台档换不起。
    """
    preferred = await _preferred_model(
        capability=capability,
        org_id=org_id,
        project_id=project_id,
        allow_reasoning=allow_reasoning,
    )
    resolution = await _resolve(capability, org_id=org_id, preferred_model_id=preferred)
    try:
        return await _attempt(capability, method, request, resolution)
    except AppError as exc:
        if resolution.key_source is KeySource.ORG:
            raise _as_byok_error(exc, resolution) from exc
        raise


async def _attempt(capability: str, method: str, request: object, resolution: Resolution) -> object:
    attempts: list[Attempt] = []
    last: AppError | None = None

    for route in await _candidates(resolution):
        provider: TextProvider | ImageProvider = route.factory()
        try:
            result = await getattr(provider, method)(request)
        except AppError as exc:
            await breaker.record_failure(route.breaker_scope)
            attempts.append(Attempt(route.provider_id, route.model_id, exc.code))
            last = exc
            spec = ERRORS.get(exc.code)
            same_account = route.key_source is KeySource.ORG and exc.code in _ACCOUNT_LEVEL_CODES
            if spec is None or not spec.failover or same_account:
                log.warning(
                    "gateway.no_failover",
                    capability=capability,
                    provider=route.provider_id,
                    key_source=route.key_source.value,
                    code=exc.code,
                )
                raise
            log.warning(
                "gateway.failover",
                capability=capability,
                from_provider=route.provider_id,
                key_source=route.key_source.value,
                code=exc.code,
            )
            continue

        await breaker.record_success(route.breaker_scope)
        if attempts:
            log.info(
                "gateway.recovered_via_failover",
                capability=capability,
                used=route.provider_id,
                failed=[a.provider_id for a in attempts],
            )
        return result

    raise last or AppError("provider.unavailable", message=f"{capability} 的所有 Provider 都不可用")


def _as_byok_error(exc: AppError, resolution: Resolution) -> AppError:
    """把上游错误重写成"是你自己那把 Key 的问题"。

    两件事必须在这里做，缺一不可：

    1. **换错误码**。原码的 user_message 是"服务暂时不可用，请稍后重试"——
       对平台档是对的，对 BYOK 是彻底的误导：等下去不会好，只有他自己
       能修。上游到底是哪种失败（鉴权/欠费/限流/参数）仍然保留在
       `detail.upstream_code` 里，分类沿用"测试连接"那一套，不另造一份。
    2. **脱敏**。上游 401 常把收到的 Key 原样贴回来，而这条 message 会被
       runner 写进 `agent_runs.error_detail`、被日志记下来。不抹掉就等于
       把用户的明文 Key 落了库。
    """
    if exc.code == "provider.byok.rejected":
        return exc
    return AppError(
        "provider.byok.rejected",
        message=probe.redact(exc.message, resolution.secret),
        detail={
            "capability": resolution.capability,
            "upstream_code": exc.code,
            "key_source": KeySource.ORG.value,
        },
    )
