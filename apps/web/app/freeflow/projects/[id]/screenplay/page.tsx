"use client";

import { use } from "react";

import { ProjectHeader } from "@/components/freeflow/project-header";
import { ScreenplayView } from "@/components/project/screenplay-view";
import { useProjectOutput } from "@/lib/freeflow/use-project-output";

// REQ-001：复用现有 ScreenplayView，不重新设计。
export default function FreeflowScreenplayPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { project, output, loading, error } = useProjectOutput(id);

  return (
    <>
      <ProjectHeader projectId={id} title={project?.title ?? "…"} />
      <main className="min-h-0 flex-1 overflow-y-auto p-6">
        {error && <p className="text-sm text-danger">{error}</p>}
        {!error && loading && <p className="text-sm text-fg-subtle">加载中…</p>}
        {!error && !loading && !output.screenplay && (
          <p className="text-sm text-fg-subtle">还没有剧本产出。</p>
        )}
        {output.screenplay && <ScreenplayView data={output.screenplay} />}
      </main>
    </>
  );
}
