"use client";

import { use } from "react";

import { ProjectHeader } from "@/components/freeflow/project-header";
import { ScenesView } from "@/components/project/scenes-view";
import { useProjectOutput } from "@/lib/freeflow/use-project-output";

// REQ-001：复用现有 ScenesView（只读列出，场景出图后端还没做，见 CLAUDE.md S11）。
export default function FreeflowScenesPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { project, output, loading, error } = useProjectOutput(id);

  return (
    <>
      <ProjectHeader projectId={id} title={project?.title ?? "…"} />
      <main className="min-h-0 flex-1 overflow-y-auto p-6">
        {error && <p className="text-sm text-danger">{error}</p>}
        {!error && loading && <p className="text-sm text-fg-subtle">加载中…</p>}
        {!error && !loading && !output.scenes && (
          <p className="text-sm text-fg-subtle">还没有场景档案。</p>
        )}
        {output.scenes && <ScenesView data={output.scenes} />}
      </main>
    </>
  );
}
