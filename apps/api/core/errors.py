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
