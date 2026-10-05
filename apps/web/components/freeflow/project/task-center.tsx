"use client";

import { useEffect, useState } from "react";

import { TaskLedger } from "@/components/freeflow/tasks/task-ledger";
import { projects, type Task } from "@/lib/api";
import { subjectsByTask, type TaskSubject } from "@/lib/freeflow/task-scope";
import type { TasksState } from "@/lib/freeflow/use-tasks";

/**
 * 项目的执行队列。
 *
 * **数据源是 `tasks`，不是 `agent_runs`**（CLAUDE.md：执行状态只认
 * `tasks.status`；08_TASK_REALTIME.md §7）。出图 / 视频 / 配音 / 合成
 * 根本不产生 `agent_runs`——用 Run 列表当任务中心，逐镜生产的每一步都看不见。
 *
 * 状态与进度走项目 SSE 增量覆盖，不轮询；行的其余字段（类型、成本、创建
 * 时间）来自 `GET /tasks`，事件负载里没有它们。
 *
 * 对象（哪个角色 / 场景 / 镜头）只在 `GET /projects/{id}/images` 里，按 task_id 反查。
 * 拿不到就退回类型名，不猜——这一列是让行好读，失败了不该影响任务列表本身。
 */
export function TaskCenter({
  tasks,
  projectId,
  focusId = null,
}: {
  /** 由页面持有：右栏也要同一份任务，两处各调一次 hook 会发两遍 `GET /tasks`。 */
  tasks: TasksState;
  projectId: string;
  focusId?: string | null;
}) {
  const [subjects, setSubjects] = useState<Map<string, TaskSubject>>(new Map());

  useEffect(() => {
    let alive = true;
    projects
      .renders(projectId)
      .then((rows) => {
        if (alive) setSubjects(subjectsByTask(rows, projectId));
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [projectId, tasks.items.length]);

  const recordHref = (task: Task) =>
    task.type === "image.generate"
      ? `/freeflow/projects/${encodeURIComponent(projectId)}/tasks?view=records&record_type=image&record_id=${encodeURIComponent(task.id)}`
      : null;

  return (
    <TaskLedger tasks={tasks} scope="project" subjects={subjects} focusId={focusId} recordHref={recordHref} />
  );
}
