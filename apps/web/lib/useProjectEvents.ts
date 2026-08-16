"use client";

import { useEffect, useRef, useState } from "react";

import { apiFetch } from "@/lib/api";

export type TaskSnapshot = {
  task_id: string;
  project_id: string | null;
  type: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  progress: number;
  attempt: number;
  error_code: string | null;
  actual_cost: number;
};

type Envelope = { type: string; project_id: string; ts: string; data: TaskSnapshot };

export type ConnectionState = "connecting" | "live" | "reconnecting" | "closed";

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
  const [tasks, setTasks] = useState<Record<string, TaskSnapshot>>({});
  const [state, setState] = useState<ConnectionState>("connecting");
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!projectId) return;

    let closed = false;
    let retry = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function loadSnapshot() {
      const page = await apiFetch<{ items: TaskSnapshot[] }>(
        `/tasks?project_id=${projectId}&limit=100`,
      ).catch(() => null);
      if (!page || closed) return;
      setTasks(Object.fromEntries(page.items.map((t) => [t.task_id ?? (t as never)["id"], t])));
    }

    async function connect() {
      if (closed) return;
      try {
        const { ticket } = await apiFetch<{ ticket: string }>(
          `/projects/${projectId}/events/ticket`,
          { method: "POST" },
        );
        if (closed) return;

        const es = new EventSource(`/api/v1/projects/${projectId}/events?ticket=${ticket}`);
        sourceRef.current = es;

        es.onopen = () => {
          retry = 0;
          setState("live");
        };

        const onTask = (e: MessageEvent<string>) => {
          const env = JSON.parse(e.data) as Envelope;
          // 整份快照覆盖，不做增量合并
          setTasks((prev) => ({ ...prev, [env.data.task_id]: env.data }));
        };

        for (const t of [
          "task.created",
          "task.started",
          "task.progress",
          "task.succeeded",
          "task.failed",
          "task.cancelled",
        ]) {
          es.addEventListener(t, onTask as EventListener);
        }

        es.addEventListener("sync.required", () => {
          // 断线期间的事件已被修剪，只能全量重取
          void loadSnapshot();
        });

        es.onerror = () => {
          es.close();
          if (closed) return;
          setState("reconnecting");
          // 指数退避，封顶 30 秒，避免服务端抖动时被客户端打垮
          retry += 1;
          timer = setTimeout(connect, Math.min(1000 * 2 ** retry, 30_000));
        };
      } catch {
        if (closed) return;
        setState("reconnecting");
        retry += 1;
        timer = setTimeout(connect, Math.min(1000 * 2 ** retry, 30_000));
      }
    }

    void loadSnapshot().then(connect);

    return () => {
      closed = true;
      setState("closed");
      if (timer) clearTimeout(timer);
      sourceRef.current?.close();
    };
  }, [projectId]);

  return { tasks: Object.values(tasks), state };
}
