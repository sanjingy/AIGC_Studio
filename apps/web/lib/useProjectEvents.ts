"use client";

import { useEffect, useState } from "react";

import { realtime, tasks as tasksApi, type Task, type TaskStatus } from "@/lib/api";

/**
 * SSE 事件里的任务快照。字段是 `tasks` 表的一个子集——
 * 事件负载没有 `created_at` / `estimated_cost` / `output_json`，
 * 所以它只能用来**覆盖状态**，不能当成整行任务用。完整的行走
 * `tasks.list()`，两者在 `lib/freeflow/use-tasks.ts` 里合并。
 */
export type TaskSnapshot = {
  task_id: string;
  project_id: string | null;
  type: string;
  status: TaskStatus;
  progress: number;
  attempt: number;
  error_code: string | null;
  actual_cost: number;
};

type Envelope = { type: string; project_id: string; ts: string; data: TaskSnapshot };

export type ConnectionState = "connecting" | "live" | "reconnecting" | "closed";

const TASK_EVENTS = [
  "task.created",
  "task.started",
  "task.progress",
  "task.succeeded",
  "task.failed",
  "task.cancelled",
] as const;

/** 列表行 → 快照。列表用 `id`，事件用 `task_id`，合并前必须先对齐。 */
function snapshotOf(row: Task): TaskSnapshot {
  return {
    task_id: row.id,
    project_id: row.project_id,
    type: row.type,
    status: row.status,
    progress: row.progress,
    attempt: row.attempt,
    error_code: row.error_code,
    actual_cost: row.actual_cost,
  };
}

type Channel = {
  tasks: Record<string, TaskSnapshot>;
  state: ConnectionState;
  listeners: Set<() => void>;
  refs: number;
  source: EventSource | null;
  timer: ReturnType<typeof setTimeout> | null;
  retry: number;
  closed: boolean;
};

/**
 * 每个项目**只开一条** SSE 连接，按订阅者计数复用。
 *
 * 一个页面上同时有出图状态（`useImages`）和任务列表（`useTasks`）是常态，
 * 各开一条连接等于让服务端为同一个项目维护两份长连接、推两份同样的事件；
 * 断线重连时两条各自退避，界面上还会看到两次抖动。
 */
const channels = new Map<string, Channel>();

function emit(ch: Channel) {
  for (const notify of ch.listeners) notify();
}

async function loadSnapshot(projectId: string, ch: Channel) {
  const page = await tasksApi.list({ projectId, limit: 100 }).catch(() => null);
  if (!page || ch.closed) return;
  ch.tasks = Object.fromEntries(page.items.map((t) => [t.id, snapshotOf(t)]));
  emit(ch);
}

async function connect(projectId: string, ch: Channel) {
  if (ch.closed) return;
  try {
    const { ticket } = await realtime.ticket(projectId);
    if (ch.closed) return;

    const es = new EventSource(realtime.eventsUrl(projectId, ticket));
    ch.source = es;

    es.onopen = () => {
      ch.retry = 0;
      ch.state = "live";
      emit(ch);
    };

    const onTask = (e: MessageEvent<string>) => {
      const env = JSON.parse(e.data) as Envelope;
      // 整份快照覆盖，不做增量合并：重连必然重放事件，增量会错乱
      ch.tasks = { ...ch.tasks, [env.data.task_id]: env.data };
      emit(ch);
    };

    for (const t of TASK_EVENTS) es.addEventListener(t, onTask as EventListener);

    // 断线期间的事件已被修剪，只能全量重取
    es.addEventListener("sync.required", () => void loadSnapshot(projectId, ch));

    es.onerror = () => {
      es.close();
      if (ch.closed) return;
      ch.state = "reconnecting";
      emit(ch);
      // 指数退避，封顶 30 秒，避免服务端抖动时被客户端打垮
      ch.retry += 1;
      ch.timer = setTimeout(() => void connect(projectId, ch), Math.min(1000 * 2 ** ch.retry, 30_000));
    };
  } catch {
    if (ch.closed) return;
    ch.state = "reconnecting";
    emit(ch);
    ch.retry += 1;
    ch.timer = setTimeout(() => void connect(projectId, ch), Math.min(1000 * 2 ** ch.retry, 30_000));
  }
}

function acquire(projectId: string): Channel {
  let ch = channels.get(projectId);
  if (ch) {
    ch.refs += 1;
    return ch;
  }
  ch = {
    tasks: {},
    state: "connecting",
    listeners: new Set(),
    refs: 1,
    source: null,
    timer: null,
    retry: 0,
    closed: false,
  };
  channels.set(projectId, ch);
  void loadSnapshot(projectId, ch).then(() => connect(projectId, ch as Channel));
  return ch;
}

function release(projectId: string, ch: Channel) {
  ch.refs -= 1;
  if (ch.refs > 0) return;
  ch.closed = true;
  ch.state = "closed";
  if (ch.timer) clearTimeout(ch.timer);
  ch.source?.close();
  channels.delete(projectId);
}

/**
 * 订阅项目事件流。
 *
 * 两条关键设计：
 * 1. 事件带完整快照，直接覆盖本地状态即可——重连必然产生重复事件，
 *    做增量更新会错乱。
 * 2. 收到 sync.required 说明断线太久、中间有缺口，必须全量拉取，
 *    不能假装无缝续上。
 */
export function useProjectEvents(projectId: string | null) {
  const [snapshot, setSnapshot] = useState<{ tasks: TaskSnapshot[]; state: ConnectionState }>({
    tasks: [],
    state: "connecting",
  });

  useEffect(() => {
    if (!projectId) {
      setSnapshot({ tasks: [], state: "closed" });
      return;
    }
    const ch = acquire(projectId);
    const notify = () => setSnapshot({ tasks: Object.values(ch.tasks), state: ch.state });
    ch.listeners.add(notify);
    notify();
    return () => {
      ch.listeners.delete(notify);
      release(projectId, ch);
    };
  }, [projectId]);

  return snapshot;
}
