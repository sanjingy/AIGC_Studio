"use client";

import { use } from "react";
import { usePathname } from "next/navigation";

import { AssetBrowser } from "@/components/freeflow/assets/asset-browser";
import { ProjectWorkbench } from "@/components/freeflow/project/project-workbench";
import { useImages } from "@/lib/freeflow/use-images";
import { useProjectState } from "@/lib/freeflow/use-project-state";
import { useTasks } from "@/lib/freeflow/use-tasks";

/**
 * 项目内资产：与全局资产库同一个组件，范围固定为本项目。上传挂到本项目
 * （后端 `UploadRequestIn.project_id`）；Skill 与独立角色档案不属于项目，不在这里列。
 */
export default function FreeflowAssetsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const state = useProjectState(id);
  const tasks = useTasks(id);
  const images = useImages(id);
  const pathname = usePathname();

  return (
    <ProjectWorkbench
      projectId={id}
      state={state}
      tasks={tasks}
      images={images}
      activeHref={pathname}
    >
      <AssetBrowser projectId={id} />
    </ProjectWorkbench>
  );
}
