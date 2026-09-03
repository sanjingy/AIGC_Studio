"use client";

import { use } from "react";
import { usePathname, useRouter } from "next/navigation";

import { ProjectSettings } from "@/components/freeflow/project/project-settings";
import { ProjectWorkbench } from "@/components/freeflow/project/project-workbench";
import { projects } from "@/lib/api";
import { useImages } from "@/lib/freeflow/use-images";
import { useProjectState } from "@/lib/freeflow/use-project-state";
import { useTasks } from "@/lib/freeflow/use-tasks";

/**
 * 项目设置。只剩后端真的有的东西：改名（`PATCH /projects/{id}`）、
 * 只读的路线与记录状态、模型偏好、删除项目（软删）。
 */
export default function FreeflowSettingsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const state = useProjectState(id);
  const tasks = useTasks(id);
  const images = useImages(id);
  const pathname = usePathname();

  async function handleDelete() {
    await projects.remove(id);
    router.push("/freeflow");
  }

  return (
    <ProjectWorkbench
      projectId={id}
      state={state}
      tasks={tasks}
      images={images}
      activeHref={pathname}
    >
      {state.error && (
        <p role="alert" className="m-4 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
          {state.error}
        </p>
      )}
      {!state.error && state.loading && <p className="p-6 text-sm text-fg-subtle">加载中…</p>}
      {!state.error && !state.loading && (
        <ProjectSettings project={state.project} onDelete={handleDelete} />
      )}
    </ProjectWorkbench>
  );
}
