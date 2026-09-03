"use client";

import { useEffect, useMemo, useState } from "react";
import { Loader2, RefreshCw, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { StatusChip } from "@/components/ui/status";
import { projects, type Render, type Task, type TaskStatus } from "@/lib/api";
import { taskTitle, type TasksState } from "@/lib/freeflow/use-tasks";
import { cn } from "@/lib/utils";

/**
 * 生成队列。
 *
 * **数据源是 `tasks`，不是 `agent_runs`**（CLAUDE.md：执行状态只认
 * `tasks.status`；08_TASK_REALTIME.md §7）。出图 / 视频 / 配音 / 合成
 * 根本不产生 `agent_runs`——用 Run 列表当任务中心，逐镜生产的每一步都看不见。
 *
 * 状态与进度走项目 SSE 增量覆盖，不轮询；行的其余字段（类型、成本、创建
 * 时间）来自 `GET /tasks`，事件负载里没有它们。
 */

type Bucket = "all" | "active" | "succeeded" | "failed";

const TABS: { key: Bucket; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "active", label: "进行中" },
  { key: "succeeded", label: "已完成" },
  { key: "failed", label: "失败/取消" },
];

function inBucket(status: TaskStatus, bucket: Bucket): boolean {
  if (bucket === "all") return true;
  if (bucket === "active") return status === "queued" || status === "running";
  if (bucket === "succeeded") return status === "succeeded";
  return status === "failed" || status === "cancelled";
}

const SUBJECT_LABEL: Record<Render["subject_kind"], string> = {
  character: "角色基准立绘",
  scene: "场景参考图",
  shot: "分镜首帧图",
};

function describe(render: Render): string {
  const base = SUBJECT_LABEL[render.subject_kind];
  if (render.subject_kind === "shot") return `${base} · 镜头 ${render.shot_index}`;
  return render.subject_ref ? `${base} · ${render.subject_ref}` : base;
}

/**
 * 失败任务要给用户可读的原因，不是只甩一个错误码。
 *
 * 键是 `apps/api/core/errors.py` 里的真实错误码（`<domain>.<category>.<specific>`）。
 * 只覆盖常见的几条，不穷举——查不到的落到 `null`，界面显示原始码。
 */
const FAIL_REASON: Record<string, string> = {
  "provider.transient.timeout": "上游生成超时，系统会自动重试并可能切到备用通道",
  "provider.rate_limit.exceeded": "上游限流，任务在排队等配额",
  "provider.unavailable": "上游服务不可用，正在切换备用通道",
  "provider.account.insufficient": "平台在上游的账户额度不足，这笔费用会全额退回",
  "provider.byok.rejected": "你自己配置的 API Key 调用失败，去模型页测试连接或换一把",
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

const CONNECTION_LABEL: Record<string, string> = {
  connecting: "正在连接实时通道…",
  live: "实时更新中",
  reconnecting: "实时通道断开，正在重连——进度可能滞后",
  closed: "实时通道已关闭",
};

export function TaskCenter({
  tasks,
  projectId,
}: {
  /** 由页面持有：右栏也要同一份任务，两处各调一次 hook 会发两遍 `GET /tasks`。 */
  tasks: TasksState;
  projectId: string;
}) {
  const [bucket, setBucket] = useState<Bucket>("all");
  // 出图任务的 subject 只在 /images 那条列表里。拿不到就退回类型名，
  // 不猜——这一条纯粹是让行标题好读，失败了不该影响任务列表本身。
  const [subjects, setSubjects] = useState<Map<string, string>>(new Map());

  useEffect(() => {
    let alive = true;
    projects
      .renders(projectId)
      .then((rows) => {
        if (!alive) return;
        setSubjects(
          new Map(
            rows
              .filter((r): r is Render & { task_id: string } => r.task_id !== null)
              .map((r) => [r.task_id, describe(r)]),
          ),
        );
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [projectId, tasks.items.length]);

  const visible = useMemo(
    () => tasks.items.filter((t) => inBucket(t.status, bucket)),
    [tasks.items, bucket],
  );
  const active = tasks.items.filter((t) => t.status === "queued" || t.status === "running").length;

  return (
    <div className="mx-auto flex w-full max-w-[920px] flex-col gap-3 p-6">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-sm font-semibold text-fg">生成队列</h1>
        <span className="tnum text-xs text-fg-subtle">
          {tasks.items.length} 个任务 · {active} 个进行中
        </span>
        <span
          className={cn(
            "text-xs",
            tasks.connection === "live" ? "text-fg-subtle" : "text-running",
          )}
        >
          {CONNECTION_LABEL[tasks.connection] ?? tasks.connection}
        </span>
        <Button size="sm" variant="ghost" className="ml-auto" onClick={() => void tasks.reload()}>
          <RefreshCw aria-hidden className="size-3.5" />
          刷新
        </Button>
      </div>

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

      {tasks.error && (
        <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
          {tasks.error}
        </p>
      )}
      {tasks.actionError && (
        <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
          {tasks.actionError}
        </p>
      )}

      <div className="overflow-hidden rounded-lg border border-border bg-surface">
        {tasks.loading && <p className="px-4 py-8 text-center text-sm text-fg-subtle">加载中…</p>}
        {!tasks.loading && visible.length === 0 && (
          <p className="px-4 py-8 text-center text-sm text-fg-subtle">
            {tasks.items.length === 0
              ? "这个项目还没有任务。出图、批量出图会在这里出现——文本阶段（advance / revise）是同步调用，只落 agent_runs，不建任务。"
              : "当前筛选下没有任务。"}
          </p>
        )}
        {visible.map((task) => (
          <TaskRow
            key={task.id}
            task={task}
            title={subjects.get(task.id) ?? taskTitle(task.type)}
            pending={tasks.isPending(task.id)}
            onRetry={() => void tasks.retry(task.id)}
            onCancel={() => void tasks.cancel(task.id)}
          />
        ))}
      </div>

      <p className="text-xs leading-5 text-fg-subtle">
        重试会<strong className="font-medium text-fg-muted">重新预扣一笔</strong> Credits，不是免费再跑一次。取消只对还没开始或
        正在排队的任务有意义——已经发给上游的那一段拦不住（决策记录 §11.5 裁决 6）。
      </p>
    </div>
  );
}

function TaskRow({
  task,
  title,
  pending,
  onRetry,
  onCancel,
}: {
  task: Task;
  title: string;
  pending: boolean;
  onRetry: () => void;
  onCancel: () => void;
}) {
  const reason = task.error_code ? FAIL_REASON[task.error_code] : null;
  const running = task.status === "running" || task.status === "queued";
  const cost = task.actual_cost || task.estimated_cost;

  return (
    <div className="flex items-center gap-3 border-b border-border px-4 py-3 last:border-0">
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium text-fg">
          {title}
          {task.attempt > 1 && (
            <span className="tnum ml-1.5 text-xs font-normal text-fg-subtle">
              第 {task.attempt} 次尝试
            </span>
          )}
        </div>
        <div className="tnum mt-0.5 truncate text-xs text-fg-subtle">
          {new Date(task.created_at).toLocaleString("zh-CN")}
          {cost > 0 &&
            ` · ${task.actual_cost > 0 ? "实扣" : "预估"} ${cost.toLocaleString("zh-CN")} Credits`}
        </div>
        {task.status === "failed" && (
          <p className="mt-1 text-xs text-danger">
            {reason ?? `未归类的失败：${task.error_code ?? "无错误码"}`}
            {reason && task.error_code && (
              <span className="ml-1 text-fg-subtle">（{task.error_code}）</span>
            )}
          </p>
        )}
      </div>

      {/* 进度是 `tasks.progress` 真实字段，SSE 实时推 */}
      <div className="hidden w-[140px] shrink-0 sm:block">
        {running && (
          <div className="flex flex-col gap-1">
            <div className="h-1 overflow-hidden rounded-full bg-surface-2">
              <div
                className="h-full bg-running transition-[width] duration-200"
                style={{ width: `${Math.max(2, Math.min(100, task.progress))}%` }}
              />
            </div>
            <span className="tnum text-xs text-fg-subtle">{task.progress}%</span>
          </div>
        )}
      </div>

      <div className="flex w-[92px] shrink-0 justify-end gap-1">
        {task.status === "failed" && (
          <Button size="sm" variant="ghost" disabled={pending} onClick={onRetry} title="重新预扣并再跑一次">
            {pending ? (
              <Loader2 aria-hidden className="size-3.5 animate-spin" />
            ) : (
              <RefreshCw aria-hidden className="size-3.5" />
            )}
            重试
          </Button>
        )}
        {running && (
          <Button size="sm" variant="ghost" disabled={pending} onClick={onCancel} title="取消这个任务">
            {pending ? (
              <Loader2 aria-hidden className="size-3.5 animate-spin" />
            ) : (
              <X aria-hidden className="size-3.5" />
            )}
            取消
          </Button>
        )}
      </div>

      <StatusChip status={task.status} />
    </div>
  );
}
