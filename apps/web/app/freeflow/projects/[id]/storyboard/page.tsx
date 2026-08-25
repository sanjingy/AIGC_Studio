"use client";

import { use } from "react";

import { ProjectHeader } from "@/components/freeflow/project-header";

// TODO(Worker C)：04 分镜编辑。见需求文档对应屏幕、REQ-041。
export default function FreeflowStoryboardPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <>
      <ProjectHeader projectId={id} title="…" />
      <main className="flex min-h-0 flex-1 items-center justify-center">
        <p className="text-sm text-fg-subtle">04 分镜编辑施工中</p>
      </main>
    </>
  );
}
