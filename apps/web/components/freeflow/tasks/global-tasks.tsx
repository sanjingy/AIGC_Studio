"use client";

import { useEffect, useMemo, useState } from "react";

import { TaskLedger } from "@/components/freeflow/tasks/task-ledger";
import { ApiRequestError, projects } from "@/lib/api";
import { subjectsByTask, type TaskSubject } from "@/lib/freeflow/task-scope";
import { useTasks } from "@/lib/freeflow/use-tasks";

/** 反查对象时最多读几个项目的出图列表。每个项目一次请求，任务跨很多项目时只给前面这些补对象名 */
const SUBJECT_PROJECT_CAP = 12;

/**
 * 全局任务页：本工作空间所有项目的执行任务（`GET /tasks` 不带 project_id）。
 *
 * 和项目里的「执行队列」是同一本账（`TaskLedger`），差别都是后端给的：
 * - 后端只有项目级事件流，这里**不实时**：回到这一页或点刷新时重取，界面上写明；
 * - 每行写项目名，并链到那个项目的执行队列里同一行（那边有实时进度和生成记录）。
 *
 * 文本步骤（剧本、档案）不建任务，不在这里；它们在项目的「生成记录」里。
 */
export function GlobalTasks() {
  const [projectFilter, setProjectFilter] = useState<string>("");
  const tasks = useTasks(null, { scope: "org", projectFilter: projectFilter || null });
  const [titles, setTitles] = useState<Map<string, string | null>>(new Map());
  const [projectsError, setProjectsError] = useState<string | null>(null);
  /** 项目列表读完（成功或失败）之后才去补读列表外的项目，免得同一个项目读两遍 */
  const [listDone, setListDone] = useState(false);
  const [subjects, setSubjects] = useState<Map<string, TaskSubject>>(new Map());

  useEffect(() => {
    projects
      .list()
      .then((page) => {
        const items = Array.isArray(page?.items) ? page.items : [];
        setTitles((prev) => {
          const next = new Map(prev);
          for (const p of items) next.set(p.id, p.title || "未命名项目");
          return next;
        });
      })
      .catch((cause) =>
        setProjectsError(cause instanceof ApiRequestError ? cause.error.user_message : "读取项目列表失败"),
      )
      .finally(() => setListDone(true));
  }, []);

  // 列表只取了最近 50 个项目；任务属于更早的项目时单独读一次，读不到（已删 / 不可见）记 null
  const projectIds = useMemo(
    () => [...new Set(tasks.items.map((t) => t.project_id).filter((id): id is string => Boolean(id)))],
    [tasks.items],
  );
  const unknown = projectIds.filter((id) => !titles.has(id));
  const unknownKey = unknown.join(",");
  useEffect(() => {
    if (!unknownKey || !listDone) return;
    let alive = true;
    void Promise.all(
      unknownKey.split(",").map((id) =>
        projects
          .get(id)
          .then((p) => [id, p.title || "未命名项目"] as const)
          .catch(() => [id, null] as const),
      ),
    ).then((pairs) => {
      if (!alive) return;
      setTitles((prev) => new Map([...prev, ...pairs]));
    });
    return () => {
      alive = false;
    };
    // titles 本身不进依赖：只在出现新的未知项目时补读
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [unknownKey, listDone]);

  // 对象名：只有出图任务能反查，按项目各读一次出图列表
  const imageProjects = useMemo(
    () =>
      [...new Set(tasks.items.filter((t) => t.type === "image.generate" && t.project_id).map((t) => t.project_id as string))].slice(
        0,
        SUBJECT_PROJECT_CAP,
      ),
    [tasks.items],
  );
  const imageKey = imageProjects.join(",");
  useEffect(() => {
    if (!imageKey) return;
    let alive = true;
    void Promise.all(
      imageKey.split(",").map((id) => projects.renders(id).then((rows) => subjectsByTask(rows, id)).catch(() => new Map())),
    ).then((maps) => {
      if (!alive) return;
      const merged = new Map<string, TaskSubject>();
      for (const m of maps) for (const [k, v] of m) merged.set(k, v);
      setSubjects(merged);
    });
    return () => {
      alive = false;
    };
  }, [imageKey, tasks.items.length]);

  const options = [...titles.entries()].filter((e): e is [string, string] => e[1] !== null);

  return (
    <div className="mx-auto flex w-full max-w-[980px] flex-col gap-3 p-4 sm:p-6">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-fg">任务</h1>
        <p className="mt-1 text-xs leading-5 text-fg-subtle">
          所有项目的执行任务（出图等）。剧本、档案这类文本步骤不进队列，在各项目的「生成记录」里看。
        </p>
      </div>
      {projectsError && (
        <p role="alert" className="rounded-[2px] bg-danger-soft px-3 py-2 text-sm text-danger">
          {projectsError}——任务仍可查看，只是项目名可能缺失。
        </p>
      )}
      <TaskLedger
        tasks={tasks}
        scope="org"
        subjects={subjects}
        projectTitle={(id) => titles.get(id)}
        toolbar={
          <label className="flex items-center gap-1.5 font-normal">
            <span className="sr-only">按项目筛选</span>
            <select
              aria-label="按项目筛选"
              value={projectFilter}
              onChange={(e) => setProjectFilter(e.target.value)}
              className="h-7 max-w-[200px] rounded-md border border-border bg-bg px-2 text-xs text-fg"
            >
              <option value="">全部项目</option>
              {options.map(([id, title]) => (
                <option key={id} value={id}>
                  {title}
                </option>
              ))}
            </select>
          </label>
        }
      />
    </div>
  );
}
