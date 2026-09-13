"""统一错误体系。

实现 21_ErrorTaxonomy.md：
- 错误码格式 `<domain>.<category>.<specific>`
- 响应体区分 message（给开发）与 user_message（给用户）
- 每个错误自带 retryable / 是否换 Provider / 是否退还预扣 / 是否计入废片率

最后四个属性看起来像业务字段，但把它们放在异常定义里是有意的：
重试策略和计费处置必须与错误类型一一对应，散落在各处 if/else 里
迟早会出现"某个错误漏了退款"这种财务事故。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Disposition(StrEnum):
    """预扣处置方式。"""

    KEEP = "keep"  # 继续持有预扣（重试中，或质量问题按约定收费）
    RELEASE = "release"  # 释放预扣（未产生上游成本）
    REFUND = "refund"  # 已扣款需退回（产生了成本但责任在平台）


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    code: str
    http_status: int
    user_message: str
    retryable: bool = False
    failover: bool = False
    disposition: Disposition = Disposition.RELEASE
    counts_as_waste: bool = False
    retry_after_ms: int | None = None


# ---------------------------------------------------------------- 错误目录
#
# 新增错误码必须登记到这里，不允许在业务代码里裸抛字符串。
# 表格顺序与 21_ErrorTaxonomy.md 第 2 节的处置矩阵一致。

ERRORS: dict[str, ErrorSpec] = {
    # --- 通用 ---
    "common.not_found": ErrorSpec("common.not_found", 404, "找不到请求的资源"),
    "common.forbidden": ErrorSpec("common.forbidden", 403, "没有权限执行此操作"),
    "common.validation_failed": ErrorSpec("common.validation_failed", 422, "请求参数有误"),
    "common.conflict": ErrorSpec("common.conflict", 409, "操作冲突，请刷新后重试"),
    "common.internal": ErrorSpec(
        "common.internal",
        500,
        "服务暂时不可用，请稍后重试",
        disposition=Disposition.REFUND,
    ),
    # --- 认证 ---
    "auth.credentials.invalid": ErrorSpec("auth.credentials.invalid", 401, "邮箱或密码不正确"),
    "auth.token.expired": ErrorSpec("auth.token.expired", 401, "登录已过期，请重新登录"),
    "auth.token.invalid": ErrorSpec("auth.token.invalid", 401, "登录状态无效，请重新登录"),
    "auth.email.taken": ErrorSpec("auth.email.taken", 409, "该邮箱已被注册"),
    # --- 上游 Provider（21 号文档第 2 节）---
    "provider.transient.timeout": ErrorSpec(
        "provider.transient.timeout",
        504,
        "生成超时，正在重试",
        retryable=True,
        failover=True,
        disposition=Disposition.KEEP,
    ),
    "provider.rate_limit.exceeded": ErrorSpec(
        "provider.rate_limit.exceeded",
        429,
        "生成排队中，请稍候",
        retryable=True,
        failover=True,
        disposition=Disposition.KEEP,
        retry_after_ms=2000,
    ),
    "provider.unavailable": ErrorSpec(
        "provider.unavailable",
        502,
        "正在切换备用通道",
        retryable=True,
        failover=True,
        disposition=Disposition.KEEP,
    ),
    "provider.account.insufficient": ErrorSpec(
        # 平台自己在上游欠费。用户无责，必须全退并立刻告警。
        "provider.account.insufficient",
        502,
        "服务暂时不可用，请稍后重试",
        failover=True,
        disposition=Disposition.REFUND,
    ),
    "provider.byok.rejected": ErrorSpec(
        # 用户自己配的那把 Key 调不通（ADR-027）。与上面那条
        # `account.insufficient` 的区别只有一个，但这个区别决定了谁去修：
        # 那条是**平台**在上游欠费/被拒，用户只能等；这条是**用户自己的**
        # Key 出了问题，只有他能改，所以文案必须直接把他指到设置页去。
        # 不 failover：能换的另一家用户根本没配 Key，换过去要么无 Key 可用，
        # 要么就是拿平台 Key 顶上——而计费此刻已经按 BYOK 折扣算了，
        # 顶上去等于平台掏钱买单，正是 ADR-027 要堵的那个洞。
        "provider.byok.rejected",
        502,
        "你为该能力配置的 API Key 调用失败，请到设置页测试连接、更换或移除它",
        failover=False,
        disposition=Disposition.RELEASE,
    ),
    "provider.params.invalid": ErrorSpec(
        "provider.params.invalid",
        400,
        "生成参数不被支持",
        disposition=Disposition.REFUND,
    ),
    "provider.content.rejected": ErrorSpec(
        # 上游安全策略拦截：产生了成本，但不该让用户买单
        "provider.content.rejected",
        422,
        "内容未通过安全审核，请调整描述",
        disposition=Disposition.REFUND,
        counts_as_waste=True,
    ),
    # --- 质量（不是失败，是不达标）---
    "quality.below_threshold": ErrorSpec(
        "quality.below_threshold",
        200,
        "画面质量不达标，正在重新生成",
        retryable=True,
        disposition=Disposition.KEEP,
        counts_as_waste=True,
    ),
    # --- 计费（19_UnitEconomics.md 第 7 节）---
    "billing.credit.insufficient": ErrorSpec(
        "billing.credit.insufficient", 402, "Credits 余额不足，请充值"
    ),
    "billing.budget.exceeded": ErrorSpec(
        "billing.budget.exceeded", 402, "已达到本项目预算上限，请确认是否追加"
    ),
    "billing.task_cap.exceeded": ErrorSpec(
        # 防"生成5秒写成5分钟"这类参数事故烧钱
        "billing.task_cap.exceeded",
        400,
        "单次任务成本异常，已拦截",
    ),
    "billing.daily_cap.exceeded": ErrorSpec(
        "billing.daily_cap.exceeded", 429, "已达到今日消费上限"
    ),
    # --- Agent ---
    "agent.output.schema_invalid": ErrorSpec(
        "agent.output.schema_invalid",
        500,
        "生成结果异常，正在重试",
        retryable=True,
        failover=True,
        disposition=Disposition.KEEP,
    ),
    "agent.max_steps.exceeded": ErrorSpec(
        "agent.max_steps.exceeded",
        500,
        "处理超出预期复杂度，请简化需求",
        disposition=Disposition.REFUND,
    ),
    # --- 一致性引擎 ---
    "consistency.profile.missing": ErrorSpec(
        # 出图要拿角色资产包和风格档案去合成提示词，两者都在角色档案
        # 那一步才落库。缺了就直说缺哪一步，别让用户拿到一个
        # "生成失败"却不知道该回去做什么。
        "consistency.profile.missing",
        409,
        "还没有角色设定，先完成角色档案这一步再出图",
    ),
    "consistency.base_image.invalid": ErrorSpec(
        # 用户想把资产库里的某张素材直接钉成角色立绘/场景参考图，但那份
        # 素材当不了基准图：不是图片，或者上传还没走完 complete（桶里可能
        # 根本没有字节）。这条路径不调任何 Provider，所以它是**纯参数错误**
        # ——400 而不是 404：资源确实存在且属于这个租户，只是不合用；
        # 报 404 会让用户以为自己选错了那张图。
        "consistency.base_image.invalid",
        400,
        "这份素材不能作为基准图，请选择一张已上传完成的图片",
    ),
    # --- 成品提示词（ADR-036）---
    #
    # 这一组全部是 **在花钱之前** 抛的：提示词准备失败就不建出图任务，
    # 不预扣，也绝不退回旧的确定性拼接去"修好"它——那正是 ADR-036 要
    # 废掉的东西，靠它兜底等于这条链路从来没接上过。
    "prompt.style.unlocked": ErrorSpec(
        # 项目还没锁定画风，或者锁定的那一条画风描述词是空的。没有风格词
        # 就没有"必须原样保留"的东西可校验，出的图也不属于任何一部片子。
        "prompt.style.unlocked",
        409,
        "还没有锁定画风，先在开拍前确认里选一种再出图",
    ),
    "prompt.context.incomplete": ErrorSpec(
        # 关键前置数据缺失：角色没有国籍人种、场景没有摄影主轴、镜头引用了
        # 不存在的角色或场景。**不猜**——猜错会让人种、服装、空间连同后面
        # 每一张图一起错，而那时的返工成本是"全部重出"（ADR-037）。
        "prompt.context.incomplete",
        409,
        "生成提示词需要的前置信息还不完整，请先补齐再试",
    ),
    "prompt.style_tokens.missing": ErrorSpec(
        # Agent 把系统给的风格词改写、精简或漏掉了（ADR-036 第 2、4 条）。
        # 判为输出不合格，不静默放行——放行一次，这一张图就和全片不是同一
        # 个画风，而画风漂移是废片的头号来源。
        "prompt.style_tokens.missing",
        422,
        "生成的提示词没有完整保留锁定画风，已拦下，请重新生成",
    ),
    "prompt.output.invalid": ErrorSpec(
        # 结构上过了 schema，但违反了模板的硬性要求：四视图少一格、
        # 五要素缺一项、视频提示词丢了最后那行强制声明。
        "prompt.output.invalid",
        422,
        "生成的提示词不符合模板要求，已拦下，请重新生成",
    ),
    "prompt.run.stale": ErrorSpec(
        # 指定的提示词是按一份**已经变了**的上下文准备的（改了景别、换了
        # 角色档案、动了场景锚点）。用它出图等于拿旧设定画新剧本。
        "prompt.run.stale",
        409,
        "这份提示词依据的内容已经改过了，请重新准备后再出图",
    ),
    "prompt.run.mismatch": ErrorSpec(
        # 指定的提示词不是这个对象的（别的角色、别的镜号、别的类型）。
        # 跨项目的那种在取运行记录时就已经 404 了，到不了这里。
        "prompt.run.mismatch",
        400,
        "这份提示词不属于当前对象，请重新准备",
    ),
    # --- 资产 ---
    "asset.upload.checksum_mismatch": ErrorSpec(
        "asset.upload.checksum_mismatch", 400, "文件上传不完整，请重试"
    ),
    "asset.upload.too_large": ErrorSpec("asset.upload.too_large", 413, "文件超出大小限制"),
    "asset.quota.exceeded": ErrorSpec(
        # 用户资产库容量用尽。必须在花钱生成/签发直传 URL **之前**抛出，
        # 生成完了才发现存不下等于白花一次上游调用的钱。
        "asset.quota.exceeded",
        413,
        "资产库容量已满，请先清理不用的素材",
    ),
    # --- 本机运行时（试点）---
    #
    # 这一组单独登记而不是复用 `provider.*`，只有一个理由，但它是决定性的：
    # `apiFetch` 只把 `user_message` 显示给用户，而这条链路的每一种失败，
    # **能修的人都是用户自己**（在他自己的电脑上启动连接器、登录 Codex、
    # 等订阅额度恢复）。复用"正在切换备用通道"这类文案，用户永远不知道
    # 该去点哪里。
    #
    # 全部 `failover=False`：本机来源被选中之后不许退回付费 API——
    # 用户以为自己在用订阅额度，悄悄替他调一次付费接口是拿他的钱补窟窿。
    # 全部 `RELEASE`：**失败**时这条路径没有产生任何平台侧上游成本，
    # 预扣原样退回。成功时照原价结算，与 API 出图同价——本机这条**额外**
    # 消耗的是用户自己的订阅额度，那笔账不在平台 Credits 体系里。
    # 两句话缺一句都会被读成"本机免费"，见 15_LOCAL_RUNTIME.md「计费口径」。
    "local_runtime.not_configured": ErrorSpec(
        "local_runtime.not_configured",
        409,
        "这个项目还没有开启「本机生成」，请改用平台 API 生成",
        failover=False,
    ),
    "local_runtime.offline": ErrorSpec(
        "local_runtime.offline",
        409,
        "没有检测到你电脑上的本地连接器，请先启动它再选择「本机生成」",
        failover=False,
    ),
    "local_runtime.capability_unsupported": ErrorSpec(
        "local_runtime.capability_unsupported",
        409,
        "你电脑上的本地连接器不支持这种生成（当前只支持 Codex 出图）",
        failover=False,
    ),
    "local_runtime.busy": ErrorSpec(
        "local_runtime.busy",
        429,
        "本机同时只能跑一个生成，等上一个跑完再试",
        retryable=True,
        failover=False,
        retry_after_ms=5000,
    ),
    "local_runtime.timeout": ErrorSpec(
        "local_runtime.timeout",
        504,
        "本机生成超时了，你电脑上的 Codex 没有在时限内返回",
        retryable=True,
        failover=False,
    ),
    "local_runtime.auth_required": ErrorSpec(
        # 覆盖两类：没登录/登录过期，以及**登录了但不是订阅通道**
        # （API Key 登录、或 config.toml 把内置 provider 换成了自定义端点）。
        # 连接器只回一个有界错误码，具体是哪一类印在它自己的窗口里——
        # 那句话里有用户的配置细节，不回传服务端。
        "local_runtime.auth_required",
        409,
        "你电脑上的 Codex 不是订阅登录状态（没登录、登录过期，或配置指向了按量付费端点），"
        "请在终端里跑 codex login，并在本地连接器窗口看具体原因",
        failover=False,
    ),
    "local_runtime.usage_limit": ErrorSpec(
        # 这条计的是**用户订阅账号的额度**，不是平台 Credits，
        # 也不是上游 API 的限流。文案必须说清楚是哪一个额度用完了。
        #
        # 括号里原来写的是"这条路径不消耗平台 Credits"。那句话在"失败时"
        # 这个狭义上没错（失败走 RELEASE），但用户读到的是**这条路径整体**
        # 免费——而成功时它按 `image.generate` 的原价结算，`pricing._shape`
        # 里 `image_source` 一个字都不参与定价。口径要和界面上那三处一致。
        "local_runtime.usage_limit",
        429,
        "你的 Codex 订阅额度已用完，等额度恢复后再试（本次失败不扣平台 Credits）",
        retryable=True,
        failover=False,
    ),
    "local_runtime.no_image": ErrorSpec(
        # 协议层没有"生成一张图"这个方法：出图是模型在一轮对话里自己
        # 决定调用内置 image_gen 工具的产物，它完全可以改成回一段文字。
        # 对模型这不是错误，对用户就是"点了没出图"，所以单独成一类。
        "local_runtime.no_image",
        502,
        "本机 Codex 这一轮没有画出图片，可以再试一次",
        retryable=True,
        failover=False,
    ),
    "local_runtime.failed": ErrorSpec(
        "local_runtime.failed",
        502,
        "本机生成失败了，可以在本地连接器的窗口里看具体原因",
        retryable=True,
        failover=False,
    ),
    "local_runtime.result_invalid": ErrorSpec(
        # 回传的不是一张认得出的图片。这条不可重试：同一个连接器再跑
        # 一次多半还是同样的产物，让用户重试只是白等。
        "local_runtime.result_invalid",
        502,
        "本机回传的结果不是一张有效图片，已丢弃",
        failover=False,
    ),
    # --- Skill ---
    "skill.spec.too_large": ErrorSpec(
        # 上限跟 skills/registry.py 的 MAX_SPEC_BYTES 是同一个数，
        # 由 service 直接引用那个常量，不在这里复制一份。
        "skill.spec.too_large",
        413,
        "Skill 文件超出大小限制",
    ),
    "skill.spec.unreadable": ErrorSpec(
        # YAML 都解析不了，连"存下来让用户看错在哪"都做不到——
        # 校验失败（能解析但不合规）是另一回事，那种照常入库。
        "skill.spec.unreadable",
        400,
        "这个文件不是合法的 YAML，请检查后重新上传",
    ),
}


class AppError(Exception):
    """业务异常。所有对外错误都必须经由它抛出。"""

    def __init__(
        self,
        code: str,
        *,
        message: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        try:
            self.spec = ERRORS[code]
        except KeyError as exc:  # 未登记的错误码 —— 开发期就要炸出来
            raise RuntimeError(f"未登记的错误码：{code}，请先在 ERRORS 中登记") from exc
        # message 给开发看，可以包含内部细节；user_message 给用户看，永远来自 spec。
        self.message = message or code
        self.detail = detail or {}
        super().__init__(self.message)

    @property
    def code(self) -> str:
        return self.spec.code

    @property
    def http_status(self) -> int:
        # quality.below_threshold 的 200 是内部语义，真要走 HTTP 时按 500 处理
        return self.spec.http_status if self.spec.http_status >= 400 else 500

    def to_payload(self, trace_id: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "code": self.spec.code,
            "message": self.message,
            "user_message": self.spec.user_message,
            "retryable": self.spec.retryable,
            "trace_id": trace_id,
        }
        if self.spec.retry_after_ms is not None:
            body["retry_after_ms"] = self.spec.retry_after_ms
        if self.detail:
            body["detail"] = self.detail
        return {"error": body}
