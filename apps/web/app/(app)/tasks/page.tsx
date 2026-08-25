"use client";

import { useEffect, useState } from "react";

import { LiveIndicator } from "@/components/live-indicator";
import { Button } from "@/components/ui/button";
import { Panel, PanelHeader } from "@/components/ui/panel";
import { StatusChip, type Status } from "@/components/ui/status";
import { apiFetch, ApiRequestError } from "@/lib/api";
import { useProjectEvents, type TaskSnapshot } from "@/lib/useProjectEvents";
import { cn, creditsToYuan } from "@/lib/utils";
import { PageScroll } from "@/components/shell/page-scroll";

type Project = { id: string; title: string };

export default function TasksPage() {
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { tasks, state } = useProjectEvents(project?.id ?? null);

  useEffect(() => {
    apiFetch<{ items: Project[] }>("/projects?limit=1")
      .then((page) => setProject(page.items[0] ?? null))
      .catch((e) => setError(e instanceof ApiRequestError ? e.error.user_message : "加载失败"));
  }, []);

  async function runMock(type: "mock.echo" | "mock.fail") {
    if (!project) return;
    await apiFetch("/tasks", {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({
        type,
        project_id: project.id,
        input: type === "mock.echo" ? { steps: 6, step_delay: 0.5 } : {},
      }),
    }).catch((e) =>
      setError(e instanceof ApiRequestError ? e.error.user_message : "创建任务失败"),
    );
  }

  if (error) {
    return (
      <p role="alert" className="m-4 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
        {error}
      </p>
    );
  }

  if (!project) {
    return <p className="p-4 text-sm text-fg-muted">还没有项目。先创建一个项目再来看任务。</p>;
  }

  const running = tasks.filter((t) => t.status === "running").length;

  return (
    <PageScroll>
      <div className="mx-auto flex max-w-[1000px] flex-col gap-4">
        <Panel>
          <PanelHeader
            title="任务中心"
            meta={`${tasks.length} 条 · ${running} 个进行中`}
            action={
              <div className="flex items-center gap-3">
                <LiveIndicator state={state} />
                <Button size="sm" onClick={() => runMock("mock.echo")}>
                  跑一个任务
                </Button>
                <Button size="sm" variant="ghost" onClick={() => runMock("mock.fail")}>
                  跑一个失败任务
                </Button>
              </div>
            }
          />

          {tasks.length === 0 ? (
            <p className="px-3 py-8 text-center text-sm text-fg-subtle">
              还没有任务。点上面的按钮跑一个试试。
            </p>
          ) : (
            <ul className="divide-y divide-border">
              {tasks.map((task) => (
                <TaskRow key={task.task_id} task={task} />
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </PageScroll>
  );
}

function TaskRow({ task }: { task: TaskSnapshot }) {
  const active = task.status === "running";

  return (
    <li className="flex items-center gap-3 px-3 py-2.5">
      <StatusChip status={task.status as Status} />

      <span className="tnum w-28 shrink-0 truncate text-xs text-fg-muted">{task.type}</span>

      <div className="min-w-0 flex-1">
        <div className="h-1 overflow-hidden rounded-full bg-surface-2">
          <div
            role="progressbar"
            aria-valuenow={task.progress}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="任务进度"
            className={cn(
              "h-full transition-[width] duration-300",
              active ? "bg-running" : task.status === "failed" ? "bg-danger" : "bg-success",
            )}
            style={{ width: `${task.progress}%` }}
          />
        </div>
      </div>

      <span className="tnum w-10 shrink-0 text-right text-xs text-fg-muted">
        {task.progress}%
      </span>

      <span className="tnum w-16 shrink-0 text-right text-xs text-fg-subtle">
        {task.actual_cost ? creditsToYuan(task.actual_cost) : "—"}
      </span>

      {task.error_code && (
        <span className="w-40 shrink-0 truncate text-xs text-danger" title={task.error_code}>
          {task.error_code}
        </span>
      )}
    </li>
  );
}
