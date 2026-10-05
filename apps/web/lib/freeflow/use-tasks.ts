"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiRequestError, tasks as tasksApi, type Task } from "@/lib/api";
import {
  appendPage,
  mergeFirstPage,
  mergeLive,
  normalizeTaskPage,
  taskTypeLabel,
  type LiveSnapshot,
} from "@/lib/freeflow/task-scope";
import { currentSeq, useProjectEvents, type ConnectionState } from "@/lib/useProjectEvents";

const PAGE_SIZE = 50;

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
 * 不做轮询——项目 SSE 本来就在连着。SSE 断线重连后再整页重拉一次：
 * 断线期间建出来的任务、结算出的成本都不在事件里。
 *
 * `projectId = null` 是**全组织**范围（全局任务页）。后端没有组织级事件流，
 * 这时不订阅 SSE，`connection` 为 `"none"`，窗口回到前台时自动刷新一次。
 * `projectFilter` 只在全组织范围下用，按项目在服务端过滤。
 */
export function useTasks(projectId: string | null, opts: { scope?: "project" | "org"; projectFilter?: string | null } = {}) {
  const org = opts.scope === "org";
  const listProject = org ? (opts.projectFilter ?? null) : projectId;
  const [rows, setRows] = useState<Task[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pending, setPending] = useState<Set<string>>(new Set());
  const events = useProjectEvents(org ? null : projectId);
  const liveTasks = events.tasks;
  const connection: ConnectionState | "none" = org ? "none" : events.state;
  /** 用户点过「加载更多」：之后刷新首页要把更早的几页接回去，游标也不能被首页的覆盖 */
  const extended = useRef(false);
  /**
   * 最近一次列表请求发出时的通道序号。序号不大于它的 SSE 快照比列表旧，不再覆盖列表——
   * 否则 SSE 断着时点了重试，重拉回来的「排队中」会被通道里那条旧的「失败」盖回去。
   */
  const [listedAt, setListedAt] = useState(0);

  const reload = useCallback(async () => {
    if (!org && !projectId) return;
    const at = org ? 0 : currentSeq(projectId);
    try {
      const page = normalizeTaskPage(
        await tasksApi.list({ projectId: listProject ?? undefined, limit: PAGE_SIZE }),
      );
      // 已经「加载更多」过就把更早的那几页接回去，否则刷新一次列表就缩回第一页
      if (extended.current) {
        setRows((prev) => mergeFirstPage(page.items, prev));
      } else {
        setRows(page.items);
        setCursor(page.next_cursor);
      }
      setListedAt(at);
      setError(null);
    } catch (cause) {
      setError(cause instanceof ApiRequestError ? cause.error.user_message : "读取任务失败");
    }
  }, [org, projectId, listProject]);

  useEffect(() => {
    extended.current = false;
    setRows([]);
    setCursor(null);
    setLoading(true);
    void reload().finally(() => setLoading(false));
  }, [reload]);

  const loadMore = useCallback(async () => {
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const page = normalizeTaskPage(
        await tasksApi.list({ projectId: listProject ?? undefined, limit: PAGE_SIZE, cursor }),
      );
      extended.current = true;
      setRows((prev) => appendPage(prev, page.items));
      setCursor(page.next_cursor);
      setError(null);
    } catch (cause) {
      setError(cause instanceof ApiRequestError ? cause.error.user_message : "读取更早的任务失败");
    } finally {
      setLoadingMore(false);
    }
  }, [cursor, listProject]);

  // 全组织范围没有事件流：切回这个标签页时刷新一次，免得看着一份过时的列表去点重试
  useEffect(() => {
    if (!org) return;
    const onVisible = () => {
      if (document.visibilityState === "visible") void reload();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [org, reload]);

  // SSE 重连补过快照：再整页重拉一次完整行
  const resyncs = events.resyncs;
  useEffect(() => {
    if (!org && resyncs > 0) void reload();
  }, [org, resyncs, reload]);

  const live = useMemo(
    () =>
      new Map<string, LiveSnapshot>(
        org ? [] : liveTasks.filter((t) => (t.seq ?? 0) > listedAt).map((t) => [t.task_id, t]),
      ),
    [org, liveTasks, listedAt],
  );

  // 事件里出现了列表没有的任务：这一批快照是它建出来之前取的
  const missing = !org && liveTasks.some((t) => !rows.some((r) => r.id === t.task_id));
  useEffect(() => {
    if (missing) void reload();
  }, [missing, reload]);

  const items = useMemo(() => mergeLive(rows, live), [rows, live]);

  const act = useCallback(
    async (taskId: string, fn: () => Promise<Task>): Promise<boolean> => {
      setPending((p) => new Set(p).add(taskId));
      setActionError(null);
      try {
        await fn();
        await reload();
        return true;
      } catch (cause) {
        setActionError(
          cause instanceof ApiRequestError
            ? cause.error.user_message || cause.error.message
            : "操作失败，请稍后重试",
        );
        // 失败多半是状态已经变了（别处取消了、已经跑完了）：重拉一次，让按钮跟着真实状态走
        await reload();
        return false;
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
    /** SSE 连接状态。断线时界面要说出来，否则用户以为进度卡住了。全组织范围是 `"none"`。 */
    connection,
    hasMore: cursor !== null,
    loadingMore,
    loadMore,
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
export function taskTitle(type: string): string {
  return taskTypeLabel(type);
}

/** 终态：不会再自己变了。 */
export const TERMINAL_STATUS = ["succeeded", "failed", "cancelled"] as const;
