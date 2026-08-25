"use client";

import { use } from "react";

import { ProjectHeader } from "@/components/freeflow/project-header";
import { ProjectAssets } from "@/components/freeflow/project/project-assets";
import { useProjectOutput } from "@/lib/freeflow/use-project-output";

/**
 * 项目内素材 tab（REQ-030 的项目内切片）。
 *
 * 只列当前项目的文件与结构化产出，不做文件夹/上传/删除——那些属于
 * 全局素材库那一页，两处各做一份必然分叉。
 */
export default function FreeflowAssetsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { project } = useProjectOutput(id);

  return (
    <>
      <ProjectHeader projectId={id} title={project?.title ?? "…"} />
      <main className="min-h-0 flex-1 overflow-y-auto">
        <ProjectAssets projectId={id} />
      </main>
    </>
  );
}
