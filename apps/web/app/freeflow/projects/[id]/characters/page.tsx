"use client";

import { use } from "react";
import { usePathname } from "next/navigation";

import { AdvanceAction } from "@/components/freeflow/project/production-actions";
import {
  advancePrimaryAction,
  ProjectWorkbench,
} from "@/components/freeflow/project/project-workbench";
import { RevisePanel } from "@/components/freeflow/project/revise-panel";
import { CharactersView } from "@/components/project/characters-view";
import { useImages } from "@/lib/freeflow/use-images";
import { useProjectState } from "@/lib/freeflow/use-project-state";
import { useTasks } from "@/lib/freeflow/use-tasks";

/**
 * 角色设定。出图三条路（AI 生成 / 从资产库选 / 本地上传）由 `CharactersView` 里的
 * `RenderSlot` 提供，都走 `useImages`——全站只有这一份实现。
 */
export default function FreeflowCharactersViewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const state = useProjectState(id);
  const tasks = useTasks(id);
  const images = useImages(id);
  const pathname = usePathname();
  const data = state.output.characters;

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
            {!data && (
              <>
                <p className="text-sm text-fg-subtle">
                  还没有角色设定。推进生产跑到这一步就会出现。
                </p>
                <AdvanceAction state={state} />
              </>
            )}
            {data && <CharactersView data={data} renders={images} />}
            <RevisePanel state={state} target="characters" />
          </>
        )}
      </div>
    </ProjectWorkbench>
  );
}
