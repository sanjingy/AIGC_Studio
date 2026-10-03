"use client";

import { use, useCallback, useState } from "react";
import { usePathname } from "next/navigation";

import {
  advancePrimaryAction,
  ProjectWorkbench,
} from "@/components/freeflow/project/project-workbench";
import { ProfileWorkspace } from "@/components/freeflow/project/profile-workspace";
import { useImages } from "@/lib/freeflow/use-images";
import { useProjectState } from "@/lib/freeflow/use-project-state";
import { useTasks } from "@/lib/freeflow/use-tasks";

/**
 * 角色设定：按真实 `characters[].ref` 定位，大图与档案并排编辑。
 *
 * 与分镜、剧本同一个壳（Reelbench P2B）：对象目录 + 主区，关掉右栏与胶片阶段带。
 * 出图三条路（AI 生成 / 资产库选图 / 本地上传）都走 `useImages`，全站只有这一份实现。
 *
 * 顶栏「推进生产」是本页唯一的顶栏写入口：有未保存草稿或正在写库时必须禁用，
 * 推进会重跑下游 Agent，弹窗式的未保存确认拦不住画在壳上的按钮。
 */
export default function FreeflowCharactersViewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const state = useProjectState(id);
  const tasks = useTasks(id);
  const images = useImages(id);
  const pathname = usePathname();

  const [guard, setGuard] = useState({ dirty: false, busy: false });
  const onGuardChange = useCallback(
    (next: { dirty: boolean; busy: boolean }) =>
      setGuard((prev) => (prev.dirty === next.dirty && prev.busy === next.busy ? prev : next)),
    [],
  );

  const advance = advancePrimaryAction(state);
  const blocked = guard.dirty
    ? "工作区里有未保存的改动，先保存或放弃再推进生产"
    : guard.busy
      ? "正在写回改动，等它完成再推进生产"
      : null;
  const primaryAction = advance && blocked ? { ...advance, disabled: true, title: blocked } : advance;

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
        <ProfileWorkspace projectId={id} role="characters" state={state} renders={images} onGuardChange={onGuardChange} />
      )}
    </ProjectWorkbench>
  );
}
