"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { Loader2, RefreshCw, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { StatusChip } from "@/components/ui/status";
import type { Task } from "@/lib/api";
import {
  CANCEL_NOTICE,
  TASK_BUCKETS,
  canCancel,
  canRetry,
  costText,
  failReason,
  inBucket,
  isKnownStatus,
  retryNotice,
  taskTypeLabel,
  timeText,
  type TaskBucket,
  type TaskSubject,
} from "@/lib/freeflow/task-scope";
import type { TasksState } from "@/lib/freeflow/use-tasks";
import { cn } from "@/lib/utils";

import { ConfirmDialog } from "../project/feedback";

/**
 * 执行队列的账本。项目任务页与全局任务页共用。
 *
 * **数据源是 `tasks`**（执行状态的唯一真相）。状态、进度一律读 `tasks` 的值
 * （项目范围再叠 SSE 快照），这里不另算一套。
 *
 * 恢复动作只给接口真的允许的任务（`task-scope.ts::canCancel / canRetry`），
 * 都要先确认，确认框写清实际影响。没有批量动作：后端没有批量接口，
 * 前端循环调单条就是在替用户做一件他没看清的事。
 */

const CONNECTION_LABEL: Record<string, string> = {
  connecting: "正在连接实时通道…",
  live: "实时更新中",
  reconnecting: "实时通道断开，正在重连——重连后会自动补齐状态",
  closed: "实时通道已关闭，点刷新取最新状态",
  none: "这一页不实时更新，回到这一页或点刷新时取最新状态；实时进度在项目的执行队列里看",
};

type Pending = { kind: "retry" | "cancel"; task: Task } | null;

export function TaskLedger({
  tasks,
  scope,
  subjects,
  projectTitle,
  focusId = null,
  recordHref,
  toolbar,
}: {
  tasks: TasksState;
  scope: "project" | "org";
  /** task_id → 对象（角色 / 场景 / 镜头）。只有出图任务能查到，查不到显示任务类型 */
  subjects: Map<string, TaskSubject>;
  /** 全组织范围：这条任务属于哪个项目。`undefined` = 还在读，`null` = 读不到 */
  projectTitle?: (projectId: string) => string | null | undefined;
  /** URL 上 `?task=` 指定要定位的那一条 */
  focusId?: string | null;
  /** 出图任务对应的那条生成记录（只在项目范围给） */
  recordHref?: (task: Task) => string | null;
  /** 栏头左侧额外的控件（全局页的项目下拉） */
  toolbar?: React.ReactNode;
}) {
  const [bucket, setBucket] = useState<TaskBucket>("all");
  const [pending, setPending] = useState<Pending>(null);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const focused = useRef<string | null>(null);
  const corrected = useRef<string | null>(null);

  const visible = useMemo(() => tasks.items.filter((t) => inBucket(t.status, bucket)), [tasks.items, bucket]);
  const active = tasks.items.filter((t) => t.status === "queued" || t.status === "running").length;
  const focusMissing = Boolean(focusId) && !tasks.loading && !tasks.items.some((t) => t.id === focusId);

  // 定位那一条：只滚一次，之后用户自己翻不再被拽回去
  useEffect(() => {
    if (!focusId || focused.current === focusId) return;
    const el = document.getElementById(`task-${focusId}`);
    if (!el) return;
    focused.current = focusId;
    el.scrollIntoView({ block: "center" });
  }, [focusId, visible]);

  // 被定位的那条不在当前筛选里：切回全部，别让定位落空。每个 focusId 只校正一次，
  // 之后用户自己切筛选不再被拽回来
  useEffect(() => {
    if (!focusId || corrected.current === focusId) return;
    const hit = tasks.items.find((t) => t.id === focusId);
    if (!hit) return;
    corrected.current = focusId;
    if (!inBucket(hit.status, bucket)) setBucket("all");
  }, [focusId, tasks.items, bucket]);

  async function confirm() {
    if (!pending) return;
    setBusy(true);
    setDialogError(null);
    const ok = pending.kind === "retry" ? await tasks.retry(pending.task.id) : await tasks.cancel(pending.task.id);
    setBusy(false);
    if (ok) {
      setPending(null);
      tasks.clearActionError();
    }
  }

  // 动作失败：后端原文留在弹窗里，不关窗，免得用户以为点成了
  useEffect(() => {
    if (pending && tasks.actionError) setDialogError(tasks.actionError);
  }, [pending, tasks.actionError]);

  const pendingSubject = pending ? subjects.get(pending.task.id)?.label ?? taskTypeLabel(pending.task.type) : "";

  return (
    <>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-xs">
        <span className="tnum text-fg-subtle">
          已载入 {tasks.items.length} 个任务，{active} 个进行中
        </span>
        <span className={cn(tasks.connection === "live" || tasks.connection === "none" ? "text-fg-subtle" : "text-running")}>
          {CONNECTION_LABEL[tasks.connection] ?? tasks.connection}
        </span>
        <Button size="sm" variant="ghost" className="ml-auto" onClick={() => void tasks.reload()}>
          <RefreshCw aria-hidden className="size-3.5" />
          刷新
        </Button>
      </div>

      {tasks.error && (
        <p role="alert" className="rounded-[2px] bg-danger-soft px-3 py-2 text-sm text-danger">
          {tasks.error}
        </p>
      )}
      {tasks.actionError && !pending && (
        <p role="alert" className="rounded-[2px] bg-danger-soft px-3 py-2 text-sm text-danger">
          {tasks.actionError}
        </p>
      )}
      {focusMissing && (
        <p role="status" className="rounded-[2px] bg-surface-2 px-3 py-2 text-xs text-fg-muted">
          要定位的任务不在已载入的列表里{tasks.hasMore ? "，可以加载更早的任务再找" : "，可能属于别的项目或已不可见"}。
        </p>
      )}

      <section className="ff-ledger" aria-label="执行队列">
        <div className="ff-ledger-head flex-wrap">
          <div className="flex flex-wrap items-center gap-2">
            {toolbar}
            <div role="group" aria-label="按状态筛选" className="flex flex-wrap items-center gap-1">
              {TASK_BUCKETS.map((t) => {
                const count = tasks.items.filter((task) => inBucket(task.status, t.key)).length;
                return (
                  <button
                    key={t.key}
                    type="button"
                    aria-pressed={bucket === t.key}
                    onClick={() => setBucket(t.key)}
                    className={cn(
                      "cursor-pointer rounded-[2px] px-2.5 py-1 text-xs transition-colors duration-150",
                      bucket === t.key
                        ? "bg-primary-soft font-semibold text-primary"
                        : "text-fg-muted hover:bg-surface-3 hover:text-fg",
                    )}
                  >
                    {t.label}
                    <span className="tnum ml-1.5 text-fg-subtle">{count}</span>
                  </button>
                );
              })}
            </div>
          </div>
          <span className="hidden font-normal sm:inline">进度 / 状态</span>
        </div>
        {tasks.hasMore && (
          <p className="ff-ledger-note">筛选与条数只覆盖已载入的任务，更早的在列表底部「加载更早的任务」。</p>
        )}

        {tasks.loading && <TaskSkeleton />}
        {!tasks.loading && visible.length === 0 && (
          <div className="ff-ledger-empty">
            <p className="text-sm font-medium text-fg">
              {tasks.items.length === 0 ? "还没有执行任务" : "当前筛选下没有任务"}
            </p>
            <p className="mx-auto mt-1.5 max-w-xl leading-6">
              {tasks.items.length === 0
                ? "出图会在这里排队执行。剧本、档案等文本步骤不进队列，在生成记录里看。"
                : "切换上方筛选查看其他状态的任务。"}
            </p>
          </div>
        )}
        {!tasks.loading &&
          visible.map((task) => (
            <TaskRow
              key={task.id}
              task={task}
              scope={scope}
              subject={subjects.get(task.id) ?? null}
              projectLabel={task.project_id ? projectTitle?.(task.project_id) : null}
              focused={task.id === focusId}
              recordHref={recordHref?.(task) ?? null}
              pending={tasks.isPending(task.id)}
              onRetry={() => {
                setDialogError(null);
                tasks.clearActionError();
                setPending({ kind: "retry", task });
              }}
              onCancel={() => {
                setDialogError(null);
                tasks.clearActionError();
                setPending({ kind: "cancel", task });
              }}
            />
          ))}
        {!tasks.loading && tasks.hasMore && (
          <div className="ff-ledger-row justify-center">
            <Button size="sm" variant="ghost" disabled={tasks.loadingMore} onClick={() => void tasks.loadMore()}>
              {tasks.loadingMore && <Loader2 aria-hidden className="size-3.5 animate-spin" />}
              加载更早的任务
            </Button>
          </div>
        )}
      </section>

      <ConfirmDialog
        open={pending !== null}
        title={pending?.kind === "retry" ? "重试这个任务" : "取消这个任务"}
        description={pendingSubject}
        confirmLabel={busy ? "提交中…" : pending?.kind === "retry" ? "确认重试" : "取消这个任务"}
        cancelLabel={pending?.kind === "cancel" ? "保留任务" : "先不重试"}
        tone={pending?.kind === "cancel" ? "danger" : "primary"}
        confirmDisabled={busy}
        onCancel={() => {
          setPending(null);
          setDialogError(null);
          tasks.clearActionError();
        }}
        onConfirm={() => void confirm()}
      >
        <ul className="list-disc space-y-1 pl-4 text-xs leading-5 text-fg-muted">
          {(pending?.kind === "retry" ? retryNotice(pending.task) : CANCEL_NOTICE).map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
        {dialogError && (
          <p role="alert" className="mt-2 rounded-md bg-danger-soft px-2.5 py-1.5 text-xs leading-5 text-danger">
            {dialogError}
          </p>
        )}
      </ConfirmDialog>
    </>
  );
}

function TaskSkeleton() {
  return (
    <div role="status" aria-label="加载中" className="divide-y divide-border">
      <span className="sr-only">加载中…</span>
      {[0, 1, 2].map((row) => (
        <div key={row} className="flex items-center gap-4 px-4 py-3">
          <div className="min-w-0 flex-1 space-y-2">
            <span className="rf-skeleton block h-3 w-2/5 rounded-sm" />
            <span className="rf-skeleton block h-2.5 w-3/5 rounded-sm" />
          </div>
          <span className="rf-skeleton hidden h-1 w-28 sm:block" />
          <span className="rf-skeleton block h-5 w-14 rounded-[2px]" />
        </div>
      ))}
    </div>
  );
}

function TaskRow({
  task,
  scope,
  subject,
  projectLabel,
  focused,
  recordHref,
  pending,
  onRetry,
  onCancel,
}: {
  task: Task;
  scope: "project" | "org";
  subject: TaskSubject | null;
  projectLabel: string | null | undefined;
  focused: boolean;
  recordHref: string | null;
  pending: boolean;
  onRetry: () => void;
  onCancel: () => void;
}) {
  const running = task.status === "running" || task.status === "queued";
  const retryable = canRetry(task);
  const cost = costText(task);
  const typeLabel = taskTypeLabel(task.type);
  const queueHref = task.project_id
    ? `/freeflow/projects/${encodeURIComponent(task.project_id)}/tasks?view=queue&task=${encodeURIComponent(task.id)}`
    : null;

  return (
    <div
      id={`task-${task.id}`}
      className="ff-ledger-row"
      data-flag={focused ? "review" : task.status === "failed" ? "danger" : undefined}
      aria-current={focused ? "true" : undefined}
    >
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 text-sm font-medium text-fg">
          {subject?.href ? (
            <Link href={subject.href} className="truncate hover:text-primary hover:underline">
              {subject.label}
            </Link>
          ) : (
            <span className="truncate">{subject?.label ?? typeLabel}</span>
          )}
          {subject && <span className="text-xs font-normal text-fg-subtle">{typeLabel}</span>}
          {task.attempt > 1 && (
            <span className="tnum text-xs font-normal text-fg-subtle">第 {task.attempt} 次尝试</span>
          )}
        </div>
        <div className="mt-0.5 flex min-w-0 flex-wrap items-baseline gap-x-4 gap-y-0.5 text-xs text-fg-subtle">
          {scope === "org" && (
            <span className="truncate">
              {!task.project_id ? (
                "不属于任何项目"
              ) : projectLabel === undefined ? (
                "读取项目…"
              ) : queueHref ? (
                <Link href={queueHref} className="text-fg-muted hover:text-primary hover:underline">
                  {projectLabel ?? "项目不可见或已删除"}
                </Link>
              ) : null}
            </span>
          )}
          <span className="tnum">{timeText(task.created_at)}</span>
          {cost && <span className="tnum">{cost}</span>}
          {recordHref && (
            <Link href={recordHref} className="text-fg-muted hover:text-primary hover:underline">
              生成记录
            </Link>
          )}
        </div>
        {task.status === "failed" && (
          <p className="mt-1 text-xs leading-5 text-danger">
            {failReason(task.error_code)}
            {task.error_code && <span className="code ml-1 text-fg-subtle">{task.error_code}</span>}
            {!retryable && (
              <span className="ml-1 text-fg-muted">
                ——这类失败不能直接重试
                {subject?.href ? (
                  <>
                    ，到
                    <Link href={subject.href} className="mx-0.5 text-primary hover:underline">
                      {subject.label}
                    </Link>
                    处理后重新生成
                  </>
                ) : (
                  "，处理后到对象处重新生成"
                )}
              </span>
            )}
          </p>
        )}
      </div>

      {/* 进度是 `tasks.progress` 真实字段 */}
      <div className="hidden w-[120px] shrink-0 sm:block">
        {running && (
          <div className="flex flex-col gap-1">
            <span className="ff-meter" data-tone="running">
              <span className="transition-[width] duration-200" style={{ width: `${Math.max(2, task.progress)}%` }} />
            </span>
            <span className="tnum text-xs text-fg-subtle">{task.progress}%</span>
          </div>
        )}
      </div>

      <div className="flex min-w-[76px] shrink-0 justify-end gap-1">
        {retryable && (
          <Button size="sm" variant="ghost" disabled={pending} onClick={onRetry}>
            {pending ? <Loader2 aria-hidden className="size-3.5 animate-spin" /> : <RefreshCw aria-hidden className="size-3.5" />}
            重试
          </Button>
        )}
        {canCancel(task) && (
          <Button size="sm" variant="ghost" disabled={pending} onClick={onCancel}>
            {pending ? <Loader2 aria-hidden className="size-3.5 animate-spin" /> : <X aria-hidden className="size-3.5" />}
            取消
          </Button>
        )}
      </div>

      {isKnownStatus(task.status) ? (
        <StatusChip status={task.status} />
      ) : (
        <span className="rounded-[2px] border border-border px-2 py-0.5 text-xs text-fg-muted">{task.status}</span>
      )}
    </div>
  );
}
