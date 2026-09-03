"use client";

import { use } from "react";
import { usePathname } from "next/navigation";

import { ProjectOverview } from "@/components/freeflow/project/project-overview";
import { ProjectWorkbench } from "@/components/freeflow/project/project-workbench";
import { useImages } from "@/lib/freeflow/use-images";
import { useProjectState } from "@/lib/freeflow/use-project-state";
import { useTasks } from "@/lib/freeflow/use-tasks";

/**
 * 项目概览。
 *
 * 三份数据在页面这一层取，往下传：壳要阶段/任务/一致性，内容要产出与出图。
 * 页面自己不发请求，也不在这一层制造统计。
 *
 * 这一页**不给顶栏主按钮**：推进生产的完整入口（含第一次要填的原始素材框）
 * 就在页面正文里，同一屏上摆两颗一模一样的按钮只会让人怀疑它们不一样。
 */
export default function FreeflowProjectOverviewPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
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
      {state.error && (
        <p role="alert" className="m-4 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
          {state.error}
        </p>
      )}
      {!state.error && state.loading && <p className="p-6 text-sm text-fg-subtle">加载中…</p>}
      {!state.error && !state.loading && state.project && (
        <ProjectOverview state={state} renders={images} project={state.project} />
      )}
    </ProjectWorkbench>
  );
}
