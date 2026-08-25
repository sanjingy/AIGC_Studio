"use client";

import { use } from "react";

import { ProjectHeader } from "@/components/freeflow/project-header";
import { ProjectSettings } from "@/components/freeflow/project/project-settings";
import { useProjectOutput } from "@/lib/freeflow/use-project-output";

/**
 * 06 项目设置（需求文档「屏幕 07」）。
 *
 * 只做「基础设置」一个分区，其余三个点了给「即将支持」并说明缺什么。
 * 页面上凡是后端没有对应列的字段，都是禁用态 + 「示例值」标记——
 * 让用户以为改了会生效，比不做这一页更糟。
 */
export default function FreeflowSettingsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { project, loading, error } = useProjectOutput(id);

  return (
    <>
      <ProjectHeader projectId={id} title={project?.title ?? "…"} />
      <main className="min-h-0 flex-1 overflow-y-auto">
        {error && (
          <p role="alert" className="m-4 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
            {error}
          </p>
        )}
        {!error && loading && <p className="p-6 text-sm text-fg-subtle">加载中…</p>}
        {!error && !loading && <ProjectSettings project={project} />}
      </main>
    </>
  );
}
