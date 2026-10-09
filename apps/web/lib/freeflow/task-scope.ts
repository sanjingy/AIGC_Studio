/**
 * 任务（`tasks` 表）的纯逻辑：规整、分栏、能不能取消/重试、失败原因、对象定位。
 *
 * **不依赖 React / Next / 路径别名**，`node --test` 直接测（`tests/web-logic.test.mjs`）。
 * 类型只用 `import type`，转译后不会真的去加载 `api.ts`。
 *
 * 规则来源全部是后端（只读核对，以后端为准）：
 * - 状态机：`apps/api/modules/task/models.py::ALLOWED_TRANSITIONS`
 * - 取消 / 重试：`task/service.py::cancel_task` / `retry_task`
 * - 错误码与能否重试：`apps/api/core/errors.py::ERRORS`
 */
import type { Render, Task, TaskStatus } from "../api";

/* ------------------------------------------------------------ 规整 */

const STATUSES: readonly string[] = ["queued", "running", "succeeded", "failed", "cancelled"];

function num(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

/**
 * 一条任务行。旧记录、代理裁剪过的响应、早期 Mock 都可能少字段：
 * 数字缺了当 0，时间缺了当 null，认不出的状态**原样保留**（显示原值比编一个好）。
 * 没有 id 的行没法定位、也没法操作，直接丢掉。
 */
export function normalizeTask(raw: unknown): Task | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const id = str(r.id);
  if (!id) return null;
  return {
    id,
    project_id: str(r.project_id),
    type: str(r.type) ?? "unknown",
    status: (str(r.status) ?? "queued") as TaskStatus,
    progress: Math.max(0, Math.min(100, num(r.progress))),
    attempt: num(r.attempt),
    max_attempts: num(r.max_attempts),
    error_code: str(r.error_code),
    estimated_cost: num(r.estimated_cost),
    actual_cost: num(r.actual_cost),
    counts_as_waste: r.counts_as_waste === true,
    output_json: r.output_json && typeof r.output_json === "object" ? (r.output_json as Record<string, unknown>) : null,
    created_at: str(r.created_at),
    started_at: str(r.started_at),
    finished_at: str(r.finished_at),
  };
}

export type TaskPageData = { items: Task[]; next_cursor: string | null };

export function normalizeTaskPage(raw: unknown): TaskPageData {
  const r = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const items = Array.isArray(r.items) ? r.items.map(normalizeTask).filter((t): t is Task => t !== null) : [];
  return { items, next_cursor: str(r.next_cursor) };
}

export function isKnownStatus(status: string): status is TaskStatus {
  return STATUSES.includes(status);
}

/* ------------------------------------------------------------ 分栏 */

/** 失败和取消分开：一个可能能重试，一个一定不能，混在一栏里用户分不清该点哪个。 */
export type TaskBucket = "all" | "active" | "succeeded" | "failed" | "cancelled";

export const TASK_BUCKETS: { key: TaskBucket; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "active", label: "进行中" },
  { key: "succeeded", label: "已完成" },
  { key: "failed", label: "失败" },
  { key: "cancelled", label: "已取消" },
];

export function inBucket(status: string, bucket: TaskBucket): boolean {
  if (bucket === "all") return true;
  if (bucket === "active") return status === "queued" || status === "running";
  return status === bucket;
}

export const STATUS_LABEL: Record<TaskStatus, string> = {
  queued: "排队中",
  running: "生成中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

/* ------------------------------------------------------------ 恢复动作 */

/** `ALLOWED_TRANSITIONS`：queued / running 都能到 cancelled。 */
export function canCancel(task: Pick<Task, "status">): boolean {
  return task.status === "queued" || task.status === "running";
}

/**
 * `core/errors.py` 里**登记了且 `retryable=False`** 的错误码。
 *
 * `retry_task` 的规则是：只有 failed 能重试；错误码在目录里且不可重试 → 409；
 * 空码或目录里没有的码 → 放行。`TaskOut` 不带 `retryable`，所以前端镜像这张表。
 * 单测会读 `errors.py` 原文逐条比对，后端加码不同步这里测试就红。
 */
export const NON_RETRYABLE_ERRORS: ReadonlySet<string> = new Set([
  "common.not_found",
  "common.forbidden",
  "common.validation_failed",
  "common.conflict",
  "common.internal",
  "auth.credentials.invalid",
  "auth.token.expired",
  "auth.token.invalid",
  "auth.email.taken",
  "provider.not_configured",
  "provider.account.insufficient",
  "provider.byok.rejected",
  "provider.params.invalid",
  "provider.content.rejected",
  "billing.credit.insufficient",
  "billing.budget.exceeded",
  "billing.task_cap.exceeded",
  "billing.daily_cap.exceeded",
  "agent.max_steps.exceeded",
  "agent.source.required",
  "agent.source.locked",
  "agent.source.conflict",
  "agent.run.superseded",
  "consistency.profile.missing",
  "consistency.base_image.invalid",
  "prompt.style.unlocked",
  "prompt.context.incomplete",
  "prompt.style_tokens.missing",
  "prompt.output.invalid",
  "prompt.run.stale",
  "prompt.run.mismatch",
  "asset.upload.checksum_mismatch",
  "asset.upload.too_large",
  "asset.quota.exceeded",
  "local_runtime.not_configured",
  "local_runtime.offline",
  "local_runtime.capability_unsupported",
  "local_runtime.auth_required",
  "local_runtime.result_invalid",
  "local_runtime.text_not_configured",
  "local_runtime.text_offline",
  "local_runtime.text_auth_required",
  "skill.spec.too_large",
  "skill.spec.unreadable",
]);

export function canRetry(task: Pick<Task, "status" | "error_code">): boolean {
  if (task.status !== "failed") return false;
  return !task.error_code || !NON_RETRYABLE_ERRORS.has(task.error_code);
}

/** 重试确认框里的几句话。数字来自 `estimated_cost`，不估。 */
export function retryNotice(task: Pick<Task, "estimated_cost">): string[] {
  const cost = num(task.estimated_cost);
  return [
    cost > 0
      ? `会重新预扣 ${cost.toLocaleString("zh-CN")} Credits（这一类任务的预估价），成功后按实际用量结算。`
      : "会重新预扣这一类任务的预估费用，成功后按实际用量结算。",
    "沿用这条任务当时的输入（提示词与生成来源），不会用你之后改过的设定；要用新设定请到对象处重新生成。",
    "如果再次失败，预扣按失败原因退回或释放。",
  ];
}

export const CANCEL_NOTICE = [
  "取消后这条任务就结束了，不能再重试；要再生成请到对象处重新发起。",
  "预扣的 Credits 会退回。已经发给上游的那一次调用拦不住。",
];

/* ------------------------------------------------------------ 失败原因 */

/** 常见错误码的中文说明。键是 `core/errors.py` 的真实码；查不到的显示原码。 */
const FAIL_REASON: Record<string, string> = {
  "provider.transient.timeout": "上游生成超时",
  "provider.rate_limit.exceeded": "上游限流",
  "provider.unavailable": "上游服务不可用",
  "provider.not_configured": "还没有可用的模型，去模型库添加供应商并设为默认",
  "provider.account.insufficient": "平台在上游的账户额度不足，这笔费用全额退回",
  "provider.byok.rejected": "你自己配置的模型供应商调用失败，去「模型」页查看原因或改选",
  "provider.params.invalid": "生成参数不被这个模型支持",
  "provider.content.rejected": "内容被上游安全策略拦下，改一下描述再生成",
  "quality.below_threshold": "画面质量不达标，判为废片",
  "billing.credit.insufficient": "Credits 余额不足，需要先充值",
  "billing.budget.exceeded": "已到本项目预算上限",
  "billing.task_cap.exceeded": "单次任务成本异常，已拦截",
  "billing.daily_cap.exceeded": "已到今日消费上限",
  "agent.output.schema_invalid": "模型返回的结构不合法",
  "agent.max_steps.exceeded": "处理超出预期复杂度，把需求拆细一点再试",
  "consistency.profile.missing": "缺少这一步需要的角色或场景档案，先完成对应档案",
  "prompt.style.unlocked": "项目还没有锁定画风",
  "prompt.context.incomplete": "生成提示词需要的前置信息不完整",
  "prompt.style_tokens.missing": "提示词没有完整保留锁定画风",
  "prompt.output.invalid": "提示词不符合模板要求",
  "prompt.run.stale": "提示词依据的内容已经改过，需要重新准备",
  "local_runtime.offline": "没有检测到本机连接器",
  "local_runtime.busy": "本机正在跑另一个生成",
  "local_runtime.timeout": "本机生成超时",
  "local_runtime.auth_required": "本机 Codex 不是订阅登录状态",
  "local_runtime.usage_limit": "Codex 订阅额度已用完，本次不扣平台 Credits",
  "local_runtime.no_image": "本机 Codex 这一轮没有画出图片",
  "local_runtime.failed": "本机生成失败，看本地连接器窗口里的原因",
  "local_runtime.result_invalid": "本机回传的不是有效图片",
  "local_runtime.text_not_configured": "项目选了本机会员 CLI，但本机会员 CLI 没对这个项目开放",
  "local_runtime.text_offline": "本机连接器不在线，文本没有发出、也没改用付费模型",
  "local_runtime.text_auth_required": "本机 CLI 不是会员订阅登录",
  "local_runtime.text_usage_limit": "CLI 会员额度暂时用完，本机文本不扣平台 Credits",
  "local_runtime.text_timeout": "本机 CLI 写文本超时",
  "local_runtime.text_failed": "本机 CLI 这次没有写成功，看本地连接器窗口里的原因",
};

/** 失败原因一句话。空码不编原因，照实说没记录。 */
export function failReason(code: string | null): string {
  if (!code) return "没有记录失败原因";
  return FAIL_REASON[code] ?? "未归类的失败";
}

/* ------------------------------------------------------------ 类型与对象 */

/** 键是 `task/models.py::TASK_TYPES`。查不到原样显示。 */
const TYPE_LABEL: Record<string, string> = {
  "image.generate": "出图",
  "video.generate": "视频生成",
  "audio.tts": "配音",
  "timeline.render": "时间线合成",
  "mock.echo": "链路自检",
  "mock.fail": "失败路径自检",
};

export function taskTypeLabel(type: string): string {
  return TYPE_LABEL[type] ?? type;
}

export type TaskSubject = { label: string; href: string | null };

const SUBJECT_LABEL: Record<Render["subject_kind"], string> = {
  character: "角色",
  scene: "场景",
  shot: "镜头",
};

/**
 * 出图任务对应的对象。`TaskOut` 不带 `input_json`，只能靠
 * `GET /projects/{id}/images` 按 task_id 反查（那条接口给 subject_kind / ref / shot_index）。
 * 链接落点与各工作台读 URL 的参数一致：角色/场景 `?ref=`，分镜 `?shot=`。
 */
export function subjectOf(render: Pick<Render, "subject_kind" | "subject_ref" | "shot_index">, projectId: string): TaskSubject {
  const base = `/freeflow/projects/${encodeURIComponent(projectId)}`;
  const kind = SUBJECT_LABEL[render.subject_kind] ?? render.subject_kind;
  if (render.subject_kind === "shot") {
    if (render.shot_index === null || render.shot_index === undefined) return { label: kind, href: `${base}/storyboard` };
    return { label: `${kind} ${render.shot_index}`, href: `${base}/storyboard?shot=${render.shot_index}` };
  }
  const page = render.subject_kind === "character" ? "characters" : render.subject_kind === "scene" ? "scenes" : null;
  if (!page) return { label: kind, href: null };
  if (!render.subject_ref) return { label: kind, href: `${base}/${page}` };
  return { label: `${kind} ${render.subject_ref}`, href: `${base}/${page}?ref=${encodeURIComponent(render.subject_ref)}` };
}

/** task_id → 对象。接口给了非数组就当没有，任务列表本身不受影响。 */
export function subjectsByTask(renders: unknown, projectId: string): Map<string, TaskSubject> {
  const out = new Map<string, TaskSubject>();
  if (!Array.isArray(renders)) return out;
  for (const r of renders as Render[]) {
    if (!r || typeof r !== "object" || typeof r.task_id !== "string") continue;
    if (!out.has(r.task_id)) out.set(r.task_id, subjectOf(r, projectId));
  }
  return out;
}

/* ------------------------------------------------------------ 合并 */

export type LiveSnapshot = Pick<Task, "status" | "progress" | "attempt" | "error_code" | "actual_cost"> & {
  task_id: string;
};

/** SSE 快照只覆盖它带的那几列；其余字段（类型、预估、时间）以列表为准。 */
export function mergeLive(rows: Task[], live: Map<string, LiveSnapshot>): Task[] {
  return rows.map((row) => {
    const snap = live.get(row.id);
    return snap
      ? {
          ...row,
          status: snap.status,
          progress: Math.max(0, Math.min(100, num(snap.progress))),
          attempt: num(snap.attempt),
          error_code: snap.error_code ?? null,
          actual_cost: num(snap.actual_cost),
        }
      : row;
  });
}

/**
 * 重新取第一页时，别把用户已经「加载更多」出来的更早记录扔掉：
 * 新首页 + 旧列表里不在新首页、且不比新首页最后一条更新的那些。
 * 列表按 created_at 倒序（`task/repository.py`），所以按位置接上即可。
 */
export function mergeFirstPage(first: Task[], loaded: Task[]): Task[] {
  const seen = new Set(first.map((t) => t.id));
  const tail = loaded.filter((t) => !seen.has(t.id));
  if (first.length === 0) return [];
  const oldest = first.at(-1)?.created_at ?? null;
  if (!oldest) return first;
  return [...first, ...tail.filter((t) => !t.created_at || t.created_at <= oldest)];
}

/** 追加下一页，按 id 去重。 */
export function appendPage(loaded: Task[], next: Task[]): Task[] {
  const seen = new Set(loaded.map((t) => t.id));
  return [...loaded, ...next.filter((t) => !seen.has(t.id))];
}

/* ------------------------------------------------------------ 显示 */

export function timeText(value: string | null | undefined): string {
  if (!value) return "时间未记录";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "时间未记录" : d.toLocaleString("zh-CN");
}

/** 成本一栏：有实扣写实扣，否则写预估；都是 0 不写（Mock 或免费路径）。 */
export function costText(task: Pick<Task, "actual_cost" | "estimated_cost">): string | null {
  if (task.actual_cost > 0) return `实扣 ${task.actual_cost.toLocaleString("zh-CN")} Credits`;
  if (task.estimated_cost > 0) return `预估 ${task.estimated_cost.toLocaleString("zh-CN")} Credits`;
  return null;
}
