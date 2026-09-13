"use client";

import { use } from "react";
import { usePathname } from "next/navigation";

import { AnchorsGate } from "@/components/freeflow/project/anchors-gate";
import { AdvanceAction } from "@/components/freeflow/project/production-actions";
import {
  advancePrimaryAction,
  ProjectWorkbench,
} from "@/components/freeflow/project/project-workbench";
import { RevisePanel } from "@/components/freeflow/project/revise-panel";
import { ScenesView } from "@/components/project/scenes-view";
import { useImages } from "@/lib/freeflow/use-images";
import { useProjectState } from "@/lib/freeflow/use-project-state";
import { useTasks } from "@/lib/freeflow/use-tasks";

/**
 * 世界美术。出图三条路（AI 生成 / 从资产库选 / 本地上传）由 `ScenesView` 里的
 * `RenderSlot` 提供，都走 `useImages`——全站只有这一份实现。
 *
 * 门③（空间锚点与光照，ADR-037）也开在这一页：它要一次性确认的锚点卡和
 * 光照状态就是下面那份场景档案，放在别处用户只能凭记忆确认。
 */
export default function FreeflowScenesViewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const state = useProjectState(id);
  const tasks = useTasks(id);
  const images = useImages(id);
  const pathname = usePathname();
  const data = state.output.scenes;

  return (
    <ProjectWorkbench
      projectId={id}
      state={state}
      tasks={tasks}
      images={images}
      activeHref={pathname}
      primaryAction={advancePrimaryAction(state)}
    >
      <div className="mx-auto flex w-full max-w-[1120px] flex-col gap-4 p-4 lg:p-6">
        {state.error && <p className="text-sm text-danger">{state.error}</p>}
        {!state.error && state.loading && <p className="text-sm text-fg-subtle">加载中…</p>}
        {!state.error && !state.loading && (
          <>
            {/* 门③ 在场景档案上面：它是这一页现在唯一要做的事，
                而下面那份档案就是它要核对的内容。 */}
            <AnchorsGate state={state} />

            {!data && (
              <>
                <p className="text-sm text-fg-subtle">
                  还没有世界美术。推进生产跑到这一步就会出现。
                </p>
                <AdvanceAction state={state} />
              </>
            )}
            {data && <ScenesView data={data} renders={images} />}
            <RevisePanel state={state} target="scenes" />
          </>
        )}
      </div>
    </ProjectWorkbench>
  );
}
