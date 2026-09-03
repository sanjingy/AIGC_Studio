"use client";

import { use } from "react";
import { usePathname } from "next/navigation";

import {
  advancePrimaryAction,
  ProjectWorkbench,
} from "@/components/freeflow/project/project-workbench";
import { StoryWorkspace } from "@/components/freeflow/project/story-workspace";
import { useImages } from "@/lib/freeflow/use-images";
import { useProjectState } from "@/lib/freeflow/use-project-state";
import { useTasks } from "@/lib/freeflow/use-tasks";

/**
 * 故事与剧本。情节目录 + 剧本在同一页（计划 §2 的「分场剧本与故事大纲合并」），
 * 外加「确认剧本」这道门与自然语言返工。
 *
 * 门在这里和右栏都能过——同一个动作、同一份实现（`production-actions.tsx`），
 * 只是落点不同。
 */
export default function FreeflowStoryPage({ params }: { params: Promise<{ id: string }> }) {
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
      {!state.error && !state.loading && <StoryWorkspace state={state} />}
    </ProjectWorkbench>
  );
}
