"use client";

import { use, useCallback, useState } from "react";
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
 * 故事与剧本（Reelbench P2A 样板）。
 *
 * 情节目录、完整剧本、集、场四个视图在同一路由下，外加**两道门**
 * （ADR-037 的门① 开拍前确认与门② 确认剧本）与自然语言返工。
 * 版式与分镜页同构：紧凑顶栏 + 模块栏 + 左目录 + 弹性主区，
 * 所以这一页也关掉右栏与胶片阶段带——门、统计、覆盖都并进了主区。
 *
 * 顶栏那颗「推进生产」在这一页是**唯一**的顶栏写入口。工作区里只要有未保存的
 * 草稿、或者正在写库，它就必须禁用：推进会重跑下游 Agent 并换掉产出，
 * 而弹窗式的未保存确认拦不住一颗画在壳上的按钮。
 */
export default function FreeflowStoryPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const state = useProjectState(id);
  const tasks = useTasks(id);
  const images = useImages(id);
  const pathname = usePathname();

  const [guard, setGuard] = useState({ dirty: false, busy: false });
  const onGuardChange = useCallback(
    (next: { dirty: boolean; busy: boolean }) =>
      setGuard((prev) =>
        prev.dirty === next.dirty && prev.busy === next.busy ? prev : next,
      ),
    [],
  );

  const advance = advancePrimaryAction(state);
  const blocked = guard.dirty
    ? "工作区里有未保存的改动，先保存或放弃再推进生产"
    : guard.busy
      ? "正在写回改动，等它完成再推进生产"
      : null;
  const primaryAction =
    advance && blocked ? { ...advance, disabled: true, title: blocked } : advance;

  return (
    <ProjectWorkbench
      projectId={id}
      state={state}
      tasks={tasks}
      images={images}
      activeHref={pathname}
      primaryAction={primaryAction}
      showAside={false}
      showStageRail={false}
    >
      {state.error && (
        <p role="alert" className="m-4 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
          {state.error}
        </p>
      )}
      {!state.error && state.loading && <p className="p-6 text-sm text-fg-subtle">加载中…</p>}
      {!state.error && !state.loading && (
        <StoryWorkspace projectId={id} state={state} onGuardChange={onGuardChange} />
      )}
    </ProjectWorkbench>
  );
}
