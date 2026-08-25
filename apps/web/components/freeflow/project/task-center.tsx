"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { StatusChip, type Status } from "@/components/ui/status";
import { ApiRequestError, projects, type AgentRun } from "@/lib/api";
import { MOCK_TASKS, type MockTaskRow } from "@/lib/freeflow/mock-data";
import { cn } from "@/lib/utils";

import { Notice, useNotice } from "./feedback";

/**
 * 05 任务中心（需求文档「屏幕 05」、REQ-050/051）。
 *
 * 数据分两段，界面上也分两段，不混在一起：
 *
 * 1. **真实段**——`projects.runs(id)` 返回的 AgentRun。标题（agent_id/role）、
 *    状态、模型、Token（REQ-051）、失败错误码、开始时间、结束时间全是真的。
 * 2. **示例段**——`MOCK_TASKS`。设计稿里的视频/配音任务类型后端还没有，
 *    进度百分比后端也没有这一列。这几行单独放一块并标注清楚，
 *    免得读者把示例数字当成真实用量。
 *
 * 唯一在真实段里"算"出来的是耗时：`created_at` 与 `finished_at` 都是
 * 后端字段，相减即可；进行中的行按当前时间实时滚。进度百分比真的没有，
 * 所以进行中的行画的是不确定进度条，并明说"进度未落库"，不编一个数字。
 */

/** 后端 AgentRunOut（apps/api/modules/agent/schemas.py）有 finished_at，
 *  `lib/api.ts` 的 AgentRun 类型还没补上这一列。这里就地窄化而不去改共享文件
 *  ——共享基座这一轮是冻结的。真正接线时应该把它加进 lib/api.ts。 */
type RunRow = AgentRun & { finished_at?: string | null };

type Bucket = "all" | "running" | "waiting" | "done" | "failed";

const TABS: { key: Bucket; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "running", label: "进行中" },
  { key: "waiting", label: "等待中" },
  { key: "done", label: "已完成" },
  { key: "failed", label: "失败" },
];

/** AgentRun.status 目前只写三个值（runner.py）：running / succeeded / failed。
 *  其余一律归到「等待中」——后端将来加 queued 时这里不用改。 */
function bucketOf(status: string): Exclude<Bucket, "all"> {
  if (status === "running") return "running";
  if (status === "succeeded") return "done";
  if (status === "failed" || status === "cancelled") return "failed";
  return "waiting";
}

const CHIP_STATUS: Record<Exclude<Bucket, "all">, Status> = {
  running: "running",
  waiting: "queued",
  done: "succeeded",
  failed: "failed",
};

/**
 * REQ-050：失败任务要给用户可读的原因，不是只甩一个错误码。
 *
 * 键是 `apps/api/core/errors.py` 里的真实错误码（`<domain>.<category>.<specific>`）。
 * 只覆盖常见的几条，不穷举——查不到的落到 `null`，界面显示原始码，
 * 总比编一个不对的原因强。
 */
const FAIL_REASON: Record<string, string> = {
  "provider.transient.timeout": "上游生成超时，系统会自动重试并可能切到备用通道",
  "provider.rate_limit.exceeded": "上游限流，任务在排队等配额",
  "provider.unavailable": "上游服务不可用，正在切换备用通道",
  "provider.account.insufficient": "平台在上游的账户额度不足，这笔费用会全额退回",
  "provider.byok.rejected": "你自己配置的 API Key 调用失败，去「设置 › Key」测试连接或换一把",
  "provider.params.invalid": "生成参数不被这个模型支持",
  "provider.content.rejected": "内容被上游安全策略拦下，改一下描述再试",
  "quality.below_threshold": "画面质量不达标，系统判定为废片并重新生成",
  "billing.credit.insufficient": "Credits 余额不足，先充值再重试",
  "billing.budget.exceeded": "已到本项目预算上限，需要确认追加",
  "billing.daily_cap.exceeded": "已到今日消费上限，明天自动恢复",
  "agent.output.schema_invalid": "模型返回的结构不合法，正在重试",
  "agent.max_steps.exceeded": "处理超出预期复杂度，把需求拆细一点再试",
  "consistency.profile.missing": "缺角色设定，先把角色档案那一步跑完再出图",
};

/** 任务标题：agent_id 是真实的（如 `visual.storyboard.v1`），
 *  这里翻成中文，翻不了就原样显示——不猜。 */
const ROLE_LABEL: Record<string, string> = {
  router: "路线判定",
  plot_index: "情节目录",
  screenplay: "剧本",
  characters: "角色设计",
  scenes: "场景设计",
  storyboard: "分镜生成",
  qa: "质量审核",
  director: "导演编排",
};

function elapsed(from: string, to: string | null | undefined, now: number): string | null {
  const start = new Date(from).getTime();
  const end = to ? new Date(to).getTime() : now;
  const sec = Math.max(0, Math.round((end - start) / 1000));
  if (!Number.isFinite(sec)) return null;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function TaskCenter({ projectId }: { projectId: string }) {
  const [rows, setRows] = useState<RunRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [bucket, setBucket] = useState<Bucket>("all");
  const [now, setNow] = useState(() => Date.now());
  const { notice, say, clear } = useNotice();

  const reload = useCallback(async () => {
    try {
      setRows(await projects.runs(projectId));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.error.user_message : "加载失败");
    }
  }, [projectId]);

  useEffect(() => {
    setLoading(true);
    void reload().finally(() => setLoading(false));
  }, [reload]);

  const hasRunning = rows.some((r) => r.status === "running");

  // agent_runs 没有 SSE 通道——项目 SSE 推的是 `tasks` 表的快照，
  // 和 agent_runs 不是一张表，task_id 与 run_id 也对不上。所以这里只在
  // 有进行中的行时轮询，没有就完全停下，不做无意义的常驻请求。
  useEffect(() => {
    if (!hasRunning) return;
    const tick = setInterval(() => {
      setNow(Date.now());
      void reload();
    }, 5000);
    return () => clearInterval(tick);
  }, [hasRunning, reload]);

  const visible = useMemo(
    () => (bucket === "all" ? rows : rows.filter((r) => bucketOf(r.status) === bucket)),
    [rows, bucket],
  );

  const visibleMock = useMemo(
    () => (bucket === "all" ? MOCK_TASKS : MOCK_TASKS.filter((t) => t.status === bucket)),
    [bucket],
  );

  return (
    <div className="mx-auto flex w-full max-w-[920px] flex-col gap-3 p-6">
      <div className="flex items-center gap-2">
        <h1 className="text-sm font-semibold text-fg">任务中心</h1>
        <span className="tnum text-xs text-fg-subtle">
          {rows.length} 条真实运行记录 · {rows.filter((r) => r.status === "running").length} 个进行中
        </span>
        <Button
          size="sm"
          variant="ghost"
          className="ml-auto"
          onClick={() => {
            setNow(Date.now());
            void reload();
          }}
        >
          <RefreshCw aria-hidden className="size-3.5" />
          刷新
        </Button>
      </div>

      <Notice notice={notice} onClose={clear} />

      <div className="flex gap-1">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            aria-pressed={bucket === t.key}
            onClick={() => setBucket(t.key)}
            className={cn(
              "cursor-pointer rounded-lg px-3 py-1.5 text-xs transition-colors duration-150",
              bucket === t.key
                ? "bg-primary-soft font-medium text-primary"
                : "text-fg-muted hover:bg-surface-2 hover:text-fg",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {error && (
        <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}

      <section className="flex flex-col gap-1.5">
        <h2 className="text-xs font-medium text-fg-subtle">
          真实运行记录 · 来自 /projects/&#123;id&#125;/agent-runs
        </h2>
        <div className="overflow-hidden rounded-lg border border-border bg-surface">
          {loading && <p className="px-4 py-8 text-center text-sm text-fg-subtle">加载中…</p>}
          {!loading && visible.length === 0 && (
            <p className="px-4 py-8 text-center text-sm text-fg-subtle">
              {rows.length === 0
                ? "这个项目还没跑过任何 Agent。回工作台推进一步就会出现在这里。"
                : "当前筛选下没有任务。"}
            </p>
          )}
          {visible.map((r) => (
            <RunRowView key={r.id} run={r} now={now} />
          ))}
        </div>
      </section>

      <section className="flex flex-col gap-1.5">
        <h2 className="text-xs font-medium text-fg-subtle">
          示例任务 · 设计稿里的视频/配音任务类型后端还没有，进度百分比也没有落库
        </h2>
        <div className="overflow-hidden rounded-lg border border-dashed border-border-strong bg-surface">
          {visibleMock.length === 0 ? (
            <p className="px-4 py-6 text-center text-xs text-fg-subtle">
              当前筛选下没有示例任务。
            </p>
          ) : (
            visibleMock.map((t) => <MockRowView key={t.id} task={t} onAction={say} />)
          )}
        </div>
      </section>
    </div>
  );
}

function RunRowView({ run, now }: { run: RunRow; now: number }) {
  const b = bucketOf(run.status);
  const tokens = run.tokens_in + run.tokens_out;
  const time = elapsed(run.created_at, run.finished_at ?? null, now);
  const reason = run.error_code ? FAIL_REASON[run.error_code] : null;

  return (
    <div className="flex items-center gap-3 border-b border-border px-4 py-3 last:border-0">
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium text-fg">
          {ROLE_LABEL[run.role] ?? run.role}
          {run.attempts > 1 && (
            <span className="tnum ml-1.5 text-xs font-normal text-fg-subtle">
              第 {run.attempts} 次尝试
            </span>
          )}
        </div>
        <div className="mt-0.5 truncate text-xs text-fg-subtle">
          {run.agent_id}
          {run.model_id ? ` · ${run.model_id}` : " · 模型未落库"}
        </div>
        {/* REQ-050：失败要说人话。映射不到的错误码原样显示，不编原因。 */}
        {b === "failed" && (
          <p className="mt-1 text-xs text-danger">
            {reason ?? `未归类的失败：${run.error_code ?? "无错误码"}`}
            {reason && run.error_code && (
              <span className="ml-1 text-fg-subtle">（{run.error_code}）</span>
            )}
          </p>
        )}
      </div>

      {/* 进度百分比后端没有这一列。进行中画不确定进度条并说明，不编数字。 */}
      <div className="hidden w-[160px] shrink-0 sm:block">
        {b === "running" ? (
          <div className="flex flex-col gap-1">
            <div className="h-1 overflow-hidden rounded-full bg-surface-2">
              <div className="animate-pulse-soft h-full w-1/2 bg-running" />
            </div>
            <span className="text-xs text-fg-subtle">进度未落库</span>
          </div>
        ) : null}
      </div>

      {/* REQ-051：Token 消耗。tokens_in / tokens_out 都是真实字段。 */}
      <span
        className="tnum w-20 shrink-0 text-right text-xs text-fg-muted"
        title={`输入 ${run.tokens_in} · 输出 ${run.tokens_out}`}
      >
        {tokens > 0 ? `${tokens.toLocaleString("zh-CN")} tok` : "—"}
      </span>

      <span
        className="tnum w-14 shrink-0 text-right text-xs text-fg-subtle"
        title={run.finished_at ? "总耗时" : "已耗时（还没结束）"}
      >
        {time ?? "—"}
      </span>

      <StatusChip status={CHIP_STATUS[b]} />
    </div>
  );
}

const MOCK_CHIP: Record<MockTaskRow["status"], Status> = {
  running: "running",
  waiting: "queued",
  done: "succeeded",
  failed: "failed",
};

function MockRowView({
  task,
  onAction,
}: {
  task: MockTaskRow;
  onAction: (text: string) => void;
}) {
  return (
    <div className="flex items-center gap-3 border-b border-border px-4 py-3 last:border-0">
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium text-fg">{task.title}</div>
        <div className="mt-0.5 truncate text-xs text-fg-subtle">{task.model}</div>
        {task.failReason && <p className="mt-1 text-xs text-danger">{task.failReason}</p>}
      </div>

      <div className="hidden w-[160px] shrink-0 sm:block">
        {task.progress !== null && (
          <div className="h-1 overflow-hidden rounded-full bg-surface-2">
            <div
              role="progressbar"
              aria-valuenow={task.progress}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label="任务进度"
              className="h-full bg-running"
              style={{ width: `${task.progress}%` }}
            />
          </div>
        )}
      </div>

      <span className="tnum w-20 shrink-0 text-right text-xs text-fg-muted">
        {task.tokensSpent === null ? "—" : `${task.tokensSpent.toLocaleString("zh-CN")} tok`}
      </span>

      <span className="tnum w-14 shrink-0 text-right text-xs text-fg-subtle">
        {task.elapsedMs === null
          ? "—"
          : `${String(Math.floor(task.elapsedMs / 60000)).padStart(2, "0")}:${String(
              Math.floor((task.elapsedMs % 60000) / 1000),
            ).padStart(2, "0")}`}
      </span>

      <StatusChip status={MOCK_CHIP[task.status]} />

      {task.status === "failed" && (
        <div className="flex shrink-0 gap-1.5">
          <Button
            size="sm"
            onClick={() =>
              onAction(
                "这是示例任务，重试按钮没有可重试的真实任务。真实任务重试走 /tasks/{id}/retry，会重新预扣一笔，不是免费再跑一次。",
              )
            }
          >
            重试
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() =>
              onAction("换模型未接入：后端还没有可选模型目录接口，也还没有按任务改模型的入口。")
            }
          >
            换模型
          </Button>
        </div>
      )}
    </div>
  );
}
