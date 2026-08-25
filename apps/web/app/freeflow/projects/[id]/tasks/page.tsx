"use client";

import { use } from "react";

import { ProjectHeader } from "@/components/freeflow/project-header";
import { TaskCenter } from "@/components/freeflow/project/task-center";
import { useProjectOutput } from "@/lib/freeflow/use-project-output";

/**
 * 05 任务中心（需求文档「屏幕 05」、REQ-050/051）。
 *
 * 列表主体是真实的 AgentRun（`projects.runs`），示例数据单独一块并标注。
 * 项目标题复用 `useProjectOutput`——它本来就要请求 project + runs 两条，
 * 这里只取 project，多余的那次请求由 TaskCenter 自己按需轮询覆盖。
 */
export default function FreeflowTasksPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { project } = useProjectOutput(id);

  return (
    <>
      <ProjectHeader projectId={id} title={project?.title ?? "…"} />
      <main className="min-h-0 flex-1 overflow-y-auto">
        <TaskCenter projectId={id} />
      </main>
    </>
  );
}
