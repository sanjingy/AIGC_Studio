"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  ApiRequestError,
  assets,
  projects,
  tasks,
  type ImageSource,
  type Render,
  type RenderSource,
  type TaskStatus,
  stageError,
} from "@/lib/api";
import { preparedPromptRunId } from "@/lib/freeflow/prepared-prompts";
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
  /** 用户自己钉的那张没有任务，所以可空——它重试不了，也没什么可重试的。 */
  taskId: string | null;
  status: TaskStatus;
  progress: number;
  errorCode: string | null;
  assetId: string | null;
  source: RenderSource;
  /** 这张图是谁画的：平台 API 还是用户自己电脑上的 Codex。没人画时为 null。 */
  imageSource: ImageSource | null;
  /**
   * 这一版图是什么时候产生的。
   *
   * 有它才能回答"这张图是不是在分镜被改之前出的"——ADR-029 的字段级编辑
   * 只改 `current_state_json`，不会碰任何一条出图记录，所以图的过期与否
   * 只能靠两个时间戳比出来（见 `use-content-edit.ts` 的 `editedAfter`）。
   */
  createdAt: string;
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
  /**
   * 按 subject 分开记的错误。
   *
   * 上面那个 `error` 挂在中栏顶部，而角色/场景档案是在**抽屉里**看的，
   * 抽屉盖住了那条横幅——出错的时候用户只会看到按钮弹回原样，
   * 也就是"点了没反应"。所以每个出图位自己也要能显示自己的错误。
   */
  const [errors, setErrors] = useState<Map<string, string>>(new Map());
  const { tasks: liveTasks } = useProjectEvents(projectId);

  const reload = useCallback(async () => {
    if (!projectId) return;
    try {
      const got = await projects.renders(projectId);
      // 接口按契约给数组。真给了别的（上游改形状、网关塞了个错误对象、
      // 或者像验收 mock 那样对没有产出的项目回 `{}`），下面的
      // `rows.some` / `for...of` 会直接把**整个分镜页**打成运行时白屏——
      // 而这一页上还有确认门和出图入口。宁可当成"还没有出图记录"。
      setRows(Array.isArray(got) ? got : []);
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
  // 用户钉上去的那种没有任务，永远不满足这个条件，也不需要——
  // 它的 asset_id 在写库那一刻就是全的。
  const stale = rows.some(
    (r) => !r.asset_id && r.task_id !== null && live.get(r.task_id)?.status === "succeeded",
  );
  useEffect(() => {
    if (stale) void reload();
  }, [stale, reload]);

  const byKey = useMemo(() => {
    const out = new Map<string, RenderView>();
    for (const r of rows) {
      const key = keyOfRender(r);
      if (out.has(key)) continue; // 列表最新在前，第一条就是当前这一版
      const t = r.task_id === null ? undefined : live.get(r.task_id);
      out.set(key, {
        taskId: r.task_id,
        status: t?.status ?? r.status,
        progress: t?.progress ?? r.progress,
        errorCode: t?.error_code ?? r.error_code,
        assetId: r.asset_id,
        source: r.source,
        imageSource: r.image_source,
        createdAt: r.created_at,
      });
    }
    return out;
  }, [rows, live]);

  const setKeyError = useCallback((key: string, message: string | null) => {
    setErrors((prev) => {
      if (message === null && !prev.has(key)) return prev;
      const next = new Map(prev);
      if (message === null) next.delete(key);
      else next.set(key, message);
      return next;
    });
  }, []);

  /**
   * `fallback` 是**这条路径**在拿不到后端错误时该说的话。
   *
   * 四个调用点里只有 `generate` / `retry` 真的在出图；`assign` 和
   * `assignFromFile` 是钉基准图和上传。以前它们共用"出图请求失败"这一句，
   * 于是本地上传连不上对象存储时，用户看到的是"出图请求失败"——错在
   * 另一件事上，人只会去重点生成按钮。
   */
  const run = useCallback(
    async (key: string, fn: () => Promise<unknown>, fallback = "出图请求失败") => {
      setPending((p) => new Set(p).add(key));
      setError(null);
      setKeyError(key, null);
      try {
        await fn();
        await reload();
      } catch (e) {
        const message = e instanceof ApiRequestError ? e.error.user_message : fallback;
        setError(message);
        setKeyError(key, message);
      } finally {
        setPending((p) => {
          const next = new Set(p);
          next.delete(key);
          return next;
        });
      }
    },
    [reload, setKeyError],
  );

  /**
   * 一个 subject 对应哪个出图接口。三处都要用，抽出来免得漏一处。
   *
   * `prompt_run_id` 在**发请求的这一刻**去取，不从 React 状态读：用户在
   * 面板里点完「准备提示词」、面板还开着就去点出图，中间没有一次重渲染
   * 也必须带上刚准备好的那一份。取不到（没准备过、或已过期被丢掉）就不带，
   * 后端自己准备一份——老按钮的行为逐字不变。
   *
   * 分镜出图取的是 `shot_image`，不是 `shot_video`：视频提示词只能查看和
   * 复制，把它送去出图等于拿一段运镜描述去画静帧。
   */
  const post = useCallback(
    (id: string, subject: RenderSubject, source: ImageSource) => {
      const promptRunId = preparedPromptRunId(
        id,
        subject.kind === "shot" ? "shot_image" : subject.kind,
        subject.kind === "shot" ? String(subject.index) : subject.ref,
      );
      return subject.kind === "character"
        ? projects.renderCharacter(id, subject.ref, source, promptRunId)
        : subject.kind === "scene"
          ? projects.renderScene(id, subject.ref, source, promptRunId)
          : projects.renderShot(id, subject.index, source, promptRunId);
    },
    [],
  );

  /**
   * 出图。`source` 决定用平台的模型还是用户自己电脑上的 Codex。
   *
   * 来源在**这一次请求**里定下来，后端把它钉进任务；之后任务重试、
   * 页面刷新、用户改选择，都不会改变这条任务实际用的是谁。
   *
   * **`source` 是必填的，没有缺省值。** 以前它默认 `"api"`，于是分镜那两个
   * 调用点漏传编译照样是绿的：用户在角色卡上选了「本机」、界面上所有出图位
   * 都显示成本机，他去分镜点生成，实际发出去的仍是 `{"source":"api"}`——
   * 扣的是平台 Credits，而他以为在用自己的订阅额度，且没有任何提示。
   * 改成必填之后，漏传一处 tsc 就会红，这比靠人去 grep 可靠。
   */
  const generate = useCallback(
    (subject: RenderSubject, source: ImageSource) => {
      if (!projectId) return;
      const key = subjectKey(subject);
      void run(key, () => post(projectId, subject, source));
    },
    [projectId, run, post],
  );

  /**
   * 批量出图（右栏的「全部生成」）。
   *
   * **串行**发，不用 Promise.all：每一次出图都要在 ledger 上预扣一笔，
   * 并发打过去等于让同一行余额上挤十几个 `SELECT FOR UPDATE`，
   * 上游那边也会同时收到十几个请求。逐个来慢不了几秒。
   *
   * `source` 同样必填，理由见 `generate`——批量那一次正是花钱最多的一次。
   */
  const generateMany = useCallback(
    async (subjects: RenderSubject[], source: ImageSource) => {
      if (!projectId) return;
      for (const subject of subjects) {
        const key = subjectKey(subject);
        setPending((p) => new Set(p).add(key));
        try {
          setKeyError(key, null);
          await post(projectId, subject, source);
        } catch (e) {
          const message = e instanceof ApiRequestError ? e.error.user_message : "出图请求失败";
          setError(message);
          setKeyError(key, message);
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
    [projectId, reload, post, setKeyError],
  );

  /**
   * 把一张**已有**的资产钉成这个角色/场景的基准图。
   *
   * 和 `generate` 走同一套 pending / error 状态，所以两条路径在界面上的
   * 加载和报错长得一模一样——用户不需要知道一条调了 Provider、
   * 另一条只是写了一行库。
   *
   * 分镜没有这条路：`base_*_asset_id` 是角色和场景档案上的字段，
   * 镜头没有对应的"基准图"概念。调用方（RenderSlot）据此不显示入口。
   */
  const assign = useCallback(
    (subject: RenderSubject, assetId: string) => {
      if (!projectId || subject.kind === "shot") return;
      const key = subjectKey(subject);
      void run(
        key,
        () =>
          subject.kind === "character"
            ? projects.setCharacterPortrait(projectId, subject.ref, assetId)
            : projects.setSceneReference(projectId, subject.ref, assetId),
        "设为基准图失败",
      );
    },
    [projectId, run],
  );

  /**
   * 本地选一张图 → 三段式直传 → 钉成基准图。
   *
   * 上传走的是仓库现成的那条链路（`assets.upload`），不另开一套：
   * 配额闸门、MIME 白名单、大小上限都长在那条链路上，绕过去等于绕过
   * 全部这些校验。整段只在最后一步和 `assign` 汇合。
   *
   * `projectId` 传给上传接口，这样这张图属于当前项目，在项目内素材页
   * 也能找到它——它确实是为这个项目传的。
   */
  const assignFromFile = useCallback(
    (subject: RenderSubject, file: File) => {
      if (!projectId || subject.kind === "shot") return;
      const key = subjectKey(subject);
      void run(
        key,
        async () => {
          const assetId = await assets.upload(file, projectId);
          try {
            return await (subject.kind === "character"
              ? projects.setCharacterPortrait(projectId, subject.ref, assetId)
              : projects.setSceneReference(projectId, subject.ref, assetId));
          } catch (e) {
            // 图已经进了资产库，只是没钉上：说清楚，别让用户以为要重新上传
            throw stageError(e, "bind");
          }
        },
        "上传失败",
      );
    },
    [projectId, run],
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
    /** 当前项目 id。出图位要靠它判断"本机来源"在不在白名单里。 */
    projectId,
    renderOf: (subject: RenderSubject) => byKey.get(subjectKey(subject)) ?? null,
    isPending: (subject: RenderSubject) => pending.has(subjectKey(subject)),
    /** 这个出图位自己的错误。抽屉里看不到中栏那条横幅，见 `errors` 的说明。 */
    errorOf: (subject: RenderSubject) => errors.get(subjectKey(subject)) ?? null,
    generate,
    generateMany,
    assign,
    assignFromFile,
    retry,
    error,
    /** 这个项目一共出过多少张图（含失败的），项目栏的资源库磁贴用 */
    count: rows.length,
  };
}

export type Renders = ReturnType<typeof useRenders>;
