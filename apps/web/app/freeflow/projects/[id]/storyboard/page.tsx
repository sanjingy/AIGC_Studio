"use client";

import { use } from "react";
import { usePathname } from "next/navigation";

import {
  advancePrimaryAction,
  ProjectWorkbench,
} from "@/components/freeflow/project/project-workbench";
import { StoryboardEditor } from "@/components/freeflow/project/storyboard-editor";
import { useImages } from "@/lib/freeflow/use-images";
import { useProjectState } from "@/lib/freeflow/use-project-state";
import { useTasks } from "@/lib/freeflow/use-tasks";

/**
 * 镜头工作台。
 *
 * 数据全部来自真实产出：`visual.storyboard.v1` 的分镜、`visual.scene.v1`
 * 的场景名、`visual.character.v1` 的角色名；出图状态与动作走 `useImages`
 * ——和角色 / 场景两页看到的是同一份状态。
 */
export default function FreeflowStoryboardPage({ params }: { params: Promise<{ id: string }> }) {
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
      primaryAction={advancePrimaryAction(state)}
    >
      {state.error && (
        <p role="alert" className="m-4 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
          {state.error}
        </p>
      )}
      {!state.error && state.loading && <p className="p-6 text-sm text-fg-subtle">加载中…</p>}
      {!state.error && !state.loading && <StoryboardEditor state={state} renders={images} />}
    </ProjectWorkbench>
  );
}
