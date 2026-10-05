"use client";

import { useEffect, useState } from "react";

import { realtime, tasks as tasksApi, type Task, type TaskStatus } from "@/lib/api";
import { normalizeTaskPage } from "@/lib/freeflow/task-scope";

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
  /**
   * 本地收到的先后（通道内单调递增，不是服务端字段）。快照按请求**发出**时的序号记，
   * 事件按到达时记。`useTasks` 拿它判断一条快照比自己刚重拉的列表新还是旧——
   * 旧的不能盖在新列表上（比如 SSE 断着时点了重试，重拉的列表已是排队中，
   * 通道里还留着那条失败快照）。
   */
  seq?: number;
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
  /** 连上过一次之后的每次 open 都是重连，要补一次快照 */
  opened: boolean;
  /** 快照请求在途时到达的事件。快照落地后按序重放：它们比快照新 */
  buffer: TaskSnapshot[] | null;
  /** 重连后补过几次快照。`useTasks` 据此重拉完整行（新任务、成本不在事件里） */
  resyncs: number;
  /** 见 `TaskSnapshot.seq` */
  seq: number;
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

/**
 * 全量重取快照。
 *
 * 在途期间到达的事件先缓冲、落地后重放：快照请求发出之后才产生的事件，
 * 内容一定比快照新，直接被快照覆盖掉就会回退成旧状态。
 */
async function loadSnapshot(projectId: string, ch: Channel) {
  ch.buffer = ch.buffer ?? [];
  ch.seq += 1;
  const at = ch.seq;
  const raw = await tasksApi.list({ projectId, limit: 100 }).catch(() => null);
  const pending = ch.buffer ?? [];
  ch.buffer = null;
  if (ch.closed) return;
  if (raw) {
    const page = normalizeTaskPage(raw);
    ch.tasks = Object.fromEntries(page.items.map((t) => [t.id, { ...snapshotOf(t), seq: at }]));
  }
  for (const snap of pending) ch.tasks = { ...ch.tasks, [snap.task_id]: snap };
  emit(ch);
}

/** 解析一条任务事件。坏数据跳过，不让一条事件把整个通道打断。 */
function parseTaskEvent(raw: string): TaskSnapshot | null {
  try {
    const env = JSON.parse(raw) as Partial<Envelope>;
    const data = env?.data;
    return data && typeof data.task_id === "string" ? data : null;
  } catch {
    return null;
  }
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
      // 每次重连都是新 EventSource + 新票据，浏览器不会带上次的 Last-Event-ID，
      // 服务端也就不会补发、更不会发 sync.required——断线期间的事件全丢了。
      // 所以重连成功后自己补一次全量快照，不假装无缝续上。
      if (ch.opened) {
        ch.resyncs += 1;
        void loadSnapshot(projectId, ch);
      }
      ch.opened = true;
    };

    const onTask = (e: MessageEvent<string>) => {
      const parsed = parseTaskEvent(e.data);
      if (!parsed) return;
      ch.seq += 1;
      const snap = { ...parsed, seq: ch.seq };
      // 整份快照覆盖，不做增量合并：重连必然重放事件，增量会错乱
      if (ch.buffer) ch.buffer.push(snap);
      ch.tasks = { ...ch.tasks, [snap.task_id]: snap };
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
    opened: false,
    buffer: null,
    resyncs: 0,
    seq: 0,
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
/** 通道当前的序号。`useTasks` 在发起列表请求前取一次，作为「比列表新」的分界线。 */
export function currentSeq(projectId: string | null): number {
  return (projectId && channels.get(projectId)?.seq) || 0;
}

export function useProjectEvents(projectId: string | null) {
  const [snapshot, setSnapshot] = useState<{ tasks: TaskSnapshot[]; state: ConnectionState; resyncs: number }>({
    tasks: [],
    state: "connecting",
    resyncs: 0,
  });

  useEffect(() => {
    if (!projectId) {
      setSnapshot({ tasks: [], state: "closed", resyncs: 0 });
      return;
    }
    const ch = acquire(projectId);
    const notify = () => setSnapshot({ tasks: Object.values(ch.tasks), state: ch.state, resyncs: ch.resyncs });
    ch.listeners.add(notify);
    notify();
    return () => {
      ch.listeners.delete(notify);
      release(projectId, ch);
    };
  }, [projectId]);

  return snapshot;
}
