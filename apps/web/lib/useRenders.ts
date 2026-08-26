"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiRequestError, projects, tasks, type Render, type TaskStatus } from "@/lib/api";
import { useProjectEvents } from "@/lib/useProjectEvents";

/**
 * 要画什么。角色立绘和场景参考图按各自的 ref，分镜按镜号——
 * 后端 `subject_kind` 就是这三种定位方式。
 */
export type RenderSubject =
  | { kind: "character"; ref: string }
  | { kind: "scene"; ref: string }
  | { kind: "shot"; index: number };

export function subjectKey(subject: RenderSubject): string {
  return subject.kind === "shot" ? `shot:${subject.index}` : `${subject.kind}:${subject.ref}`;
}

function keyOfRender(r: Render): string {
  return r.subject_kind === "shot"
    ? `shot:${r.shot_index}`
    : `${r.subject_kind}:${r.subject_ref}`;
}

/** 一个角色/一个镜号当前这一版图的状态。 */
export type RenderView = {
  taskId: string;
  status: TaskStatus;
  progress: number;
  errorCode: string | null;
  assetId: string | null;
};

/**
 * 出图状态。
 *
 * 两个来源合起来用，各自负责它擅长的那件事：
 *
 * - `GET /projects/{id}/images` 知道"这张图属于谁"和"资产 id 是多少"，
 *   但它是一次快照。
 * - SSE（`useProjectEvents`）知道最新的状态和进度，但事件里只有任务快照，
 *   没有资产 id——出图完成的那一刻前端只知道"成了"，不知道图在哪。
 *
 * 所以：状态取 SSE 的，归属和资产 id 取列表的，任务一到终态就重取一次列表。
 * 不另外做轮询——项目页里任务进度本来就走这条 SSE。
 */
export function useRenders(projectId: string | null) {
  const [rows, setRows] = useState<Render[]>([]);
  const [pending, setPending] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const { tasks: liveTasks } = useProjectEvents(projectId);

  const reload = useCallback(async () => {
    if (!projectId) return;
    try {
      setRows(await projects.renders(projectId));
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.error.user_message : "读取出图记录失败");
    }
  }, [projectId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const live = useMemo(
    () => new Map(liveTasks.map((t) => [t.task_id, t])),
    [liveTasks],
  );

  // 跑完了但列表里还没有资产 id，说明这份快照是任务完成之前取的。
  // 重取一次就有了；取到之后这个条件自然为假，不会反复拉。
  const stale = rows.some((r) => !r.asset_id && live.get(r.task_id)?.status === "succeeded");
  useEffect(() => {
    if (stale) void reload();
  }, [stale, reload]);

  const byKey = useMemo(() => {
    const out = new Map<string, RenderView>();
    for (const r of rows) {
      const key = keyOfRender(r);
      if (out.has(key)) continue; // 列表最新在前，第一条就是当前这一版
      const t = live.get(r.task_id);
      out.set(key, {
        taskId: r.task_id,
        status: t?.status ?? r.status,
        progress: t?.progress ?? r.progress,
        errorCode: t?.error_code ?? r.error_code,
        assetId: r.asset_id,
      });
    }
    return out;
  }, [rows, live]);

  const run = useCallback(
    async (key: string, fn: () => Promise<unknown>) => {
      setPending((p) => new Set(p).add(key));
      setError(null);
      try {
        await fn();
        await reload();
      } catch (e) {
        setError(e instanceof ApiRequestError ? e.error.user_message : "出图请求失败");
      } finally {
        setPending((p) => {
          const next = new Set(p);
          next.delete(key);
          return next;
        });
      }
    },
    [reload],
  );

  /** 一个 subject 对应哪个出图接口。三处都要用，抽出来免得漏一处。 */
  const post = useCallback(
    (id: string, subject: RenderSubject) =>
      subject.kind === "character"
        ? projects.renderCharacter(id, subject.ref)
        : subject.kind === "scene"
          ? projects.renderScene(id, subject.ref)
          : projects.renderShot(id, subject.index),
    [],
  );

  const generate = useCallback(
    (subject: RenderSubject) => {
      if (!projectId) return;
      const key = subjectKey(subject);
      void run(key, () => post(projectId, subject));
    },
    [projectId, run, post],
  );

  /**
   * 批量出图（右栏的「全部生成」）。
   *
   * **串行**发，不用 Promise.all：每一次出图都要在 ledger 上预扣一笔，
   * 并发打过去等于让同一行余额上挤十几个 `SELECT FOR UPDATE`，
   * 上游那边也会同时收到十几个请求。逐个来慢不了几秒。
   */
  const generateMany = useCallback(
    async (subjects: RenderSubject[]) => {
      if (!projectId) return;
      for (const subject of subjects) {
        const key = subjectKey(subject);
        setPending((p) => new Set(p).add(key));
        try {
          await post(projectId, subject);
        } catch (e) {
          setError(e instanceof ApiRequestError ? e.error.user_message : "出图请求失败");
          break; // 余额不足这类错误，后面几张也一定失败，没必要继续刷屏
        } finally {
          setPending((p) => {
            const next = new Set(p);
            next.delete(key);
            return next;
          });
        }
      }
      await reload();
    },
    [projectId, reload, post],
  );

  /** 失败重试走任务自己的重试接口，不新建任务——它会重新预扣同一笔。 */
  const retry = useCallback(
    (subject: RenderSubject, taskId: string) => {
      const key = subjectKey(subject);
      void run(key, () => tasks.retry(taskId));
    },
    [run],
  );

  return {
    renderOf: (subject: RenderSubject) => byKey.get(subjectKey(subject)) ?? null,
    isPending: (subject: RenderSubject) => pending.has(subjectKey(subject)),
    generate,
    generateMany,
    retry,
    error,
    /** 这个项目一共出过多少张图（含失败的），项目栏的资源库磁贴用 */
    count: rows.length,
  };
}

export type Renders = ReturnType<typeof useRenders>;
