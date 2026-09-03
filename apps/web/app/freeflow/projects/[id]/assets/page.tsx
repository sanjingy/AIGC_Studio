"use client";

import { use } from "react";
import { usePathname } from "next/navigation";

import { ProjectAssets } from "@/components/freeflow/project/project-assets";
import { ProjectWorkbench } from "@/components/freeflow/project/project-workbench";
import { useImages } from "@/lib/freeflow/use-images";
import { useProjectState } from "@/lib/freeflow/use-project-state";
import { useTasks } from "@/lib/freeflow/use-tasks";

/**
 * 项目内资产。只列当前项目的文件与结构化产出，不做文件夹 / 上传 / 删除——
 * 那些在全局资产库那一页，两处各做一份必然分叉。
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
      <ProjectAssets projectId={id} />
    </ProjectWorkbench>
  );
}
