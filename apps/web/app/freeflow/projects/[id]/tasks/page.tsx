"use client";

import { use } from "react";

import { ProjectHeader } from "@/components/freeflow/project-header";

// TODO(Worker C)：05 任务中心。见需求文档对应屏幕、REQ-050/051。
export default function FreeflowTasksPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <>
      <ProjectHeader projectId={id} title="…" />
      <main className="flex min-h-0 flex-1 items-center justify-center">
        <p className="text-sm text-fg-subtle">05 任务中心施工中</p>
      </main>
    </>
  );
}
