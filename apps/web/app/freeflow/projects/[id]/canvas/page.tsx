"use client";

import { use } from "react";

import { ProjectHeader } from "@/components/freeflow/project-header";

// TODO(Worker B)：02 工作流画布——核心新功能。见需求文档「屏幕 02」、
// REQ-020~023。mock 数据在 lib/freeflow/mock-data.ts 的 MOCK_GRAPH，
// 类型在 lib/freeflow/types.ts。
export default function FreeflowCanvasPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <>
      <ProjectHeader projectId={id} title="…" onSave={() => {}} onRun={() => {}} />
      <main className="flex min-h-0 flex-1 items-center justify-center">
        <p className="text-sm text-fg-subtle">工作流画布施工中</p>
      </main>
    </>
  );
}
