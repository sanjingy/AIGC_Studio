"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiRequestError, tasks as tasksApi, type Task, type TaskStatus } from "@/lib/api";
import { useProjectEvents } from "@/lib/useProjectEvents";

/**
 * 任务队列。
 *
 * **数据源是 `tasks` 表，不是 `agent_runs`**（CLAUDE.md：执行状态只认
 * `tasks.status`）。出图 / 视频 / 配音 / 合成根本不产生 `agent_runs`，
 * 用 Run 列表当任务中心，逐镜生产的每一步都看不见。
 *
 * 两个来源各司其职：
 * - `GET /tasks` 给出完整的行（类型、预估/实际成本、创建时间）；
 * - SSE 给出最新的状态与进度，但事件负载是 `tasks` 的子集，只能覆盖状态。
 *
 * 出现列表里没有的任务 id（新任务在页面打开后才建）就重拉一次列表，
 * 不做轮询——项目 SSE 本来就在连着。
 */
export function useTasks(projectId: string | null) {
  const [rows, setRows] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pending, setPending] = useState<Set<string>>(new Set());
  const { tasks: liveTasks, state: connection } = useProjectEvents(projectId);

  const reload = useCallback(async () => {
    if (!projectId) return;
    try {
      const page = await tasksApi.list({ projectId, limit: 100 });
      setRows(page.items);
      setError(null);
    } catch (cause) {
      setError(cause instanceof ApiRequestError ? cause.error.user_message : "读取任务失败");
    }
  }, [projectId]);

  useEffect(() => {
    setLoading(true);
    void reload().finally(() => setLoading(false));
  }, [reload]);

  const live = useMemo(() => new Map(liveTasks.map((t) => [t.task_id, t])), [liveTasks]);

  // 事件里出现了列表没有的任务：这一批快照是它建出来之前取的
  const missing = liveTasks.some((t) => !rows.some((r) => r.id === t.task_id));
  useEffect(() => {
    if (missing) void reload();
  }, [missing, reload]);

  const items = useMemo(
    () =>
      rows.map((row) => {
        const snap = live.get(row.id);
        return snap
          ? {
              ...row,
              status: snap.status,
              progress: snap.progress,
              attempt: snap.attempt,
              error_code: snap.error_code,
              actual_cost: snap.actual_cost,
            }
          : row;
      }),
    [rows, live],
  );

  const act = useCallback(
    async (taskId: string, fn: () => Promise<Task>) => {
      setPending((p) => new Set(p).add(taskId));
      setActionError(null);
      try {
        await fn();
        await reload();
      } catch (cause) {
        setActionError(
          cause instanceof ApiRequestError ? cause.error.user_message : "操作失败，请稍后重试",
        );
      } finally {
        setPending((p) => {
          const next = new Set(p);
          next.delete(taskId);
          return next;
        });
      }
    },
    [reload],
  );

  return {
    items,
    loading,
    error,
    actionError,
    /** SSE 连接状态。断线时界面要说出来，否则用户以为进度卡住了。 */
    connection,
    isPending: (taskId: string) => pending.has(taskId),
    /** 重试会**重新预扣一笔**，不是免费再跑一次。 */
    retry: (taskId: string) => act(taskId, () => tasksApi.retry(taskId)),
    cancel: (taskId: string) => act(taskId, () => tasksApi.cancel(taskId)),
    reload,
    clearActionError: () => setActionError(null),
  };
}

export type TasksState = ReturnType<typeof useTasks>;

/**
 * 任务类型 → 中文。键是 `apps/api/modules/task/models.py` 的 `TASK_TYPES`。
 * 查不到就原样显示类型串——编一个名字比显示原值更糟。
 *
 * 出图三类（角色 / 场景 / 分镜）在 `tasks` 里是**同一个类型** `image.generate`，
 * 区别只在 `input_json.subject_kind`，而 `TaskOut` 不返回 `input_json`。
 * 要显示到"哪一张"这个粒度，得靠 `GET /projects/{id}/images` 按 task_id 反查
 * （任务页那样做；右栏地方太窄，只给类型名）。
 */
const TYPE_LABEL: Record<string, string> = {
  "image.generate": "出图",
  "video.generate": "视频生成",
  "audio.tts": "配音",
  "timeline.render": "时间线合成",
  "mock.echo": "链路自检",
  "mock.fail": "失败路径自检",
};

export function taskTitle(type: string): string {
  return TYPE_LABEL[type] ?? type;
}

/** 终态：不会再自己变了，重试/取消按钮据此决定显不显示。 */
export const TERMINAL_STATUS: readonly TaskStatus[] = ["succeeded", "failed", "cancelled"];
