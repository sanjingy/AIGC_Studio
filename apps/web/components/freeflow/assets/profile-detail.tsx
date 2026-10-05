"use client";

import { useId } from "react";
import Link from "next/link";
import { ArrowRight } from "lucide-react";

import { CharactersView } from "@/components/project/characters-view";
import { ScenesView } from "@/components/project/scenes-view";
import { Dialog, DialogCloseButton } from "@/components/ui/dialog";
import type { CharacterEntry, ProfileEntry } from "@/lib/api";

export type ProfileTarget = { kind: "profile"; profile: ProfileEntry } | { kind: "entry"; entry: CharacterEntry };

/**
 * 档案详情抽屉，只读。
 *
 * 项目档案来自资产库接口的 `profiles`：每个项目**最近一次运行**的产出。工作台里之后
 * 的手动修改写在项目状态里，这里看不到——所以写明这一点，并给「在项目中打开」。
 * 独立角色档案不挂项目，没有工作台可去，也出不了基准图。
 */
export function ProfileDetail({ target, onClose }: { target: ProfileTarget | null; onClose: () => void }) {
  const headingId = useId();
  const isScenes = target?.kind === "profile" && target.profile.kind === "scenes";
  const title =
    target === null
      ? ""
      : target.kind === "entry"
        ? target.entry.title
        : `${target.profile.project_title}：${isScenes ? "场景档案" : "角色档案"}`;

  return (
    <Dialog
      open={target !== null}
      onOpenChange={(next) => !next && onClose()}
      labelledBy={headingId}
      placement="right"
      className="h-full w-[min(640px,100vw)] overflow-y-auto border-l border-border-strong bg-surface"
    >
      {target && (
        <>
          <div className="sticky top-0 z-10 flex items-start justify-between gap-3 border-b border-border bg-surface px-4 py-3">
            <div className="min-w-0">
              <h2 id={headingId} className="truncate text-sm font-semibold text-fg" title={title}>
                {title}
              </h2>
              <p className="mt-0.5 text-xs text-fg-subtle">
                {target.kind === "entry" ? "独立角色档案，不属于任何项目" : "项目档案，最近一次生成的版本"}
              </p>
            </div>
            <DialogCloseButton onClick={onClose} />
          </div>

          {target.kind === "profile" ? (
            <div className="flex flex-col gap-2 border-b border-border px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-xs leading-5 text-fg-subtle">之后在工作台里的手动修改以项目为准。</p>
              <Link
                href={`/freeflow/projects/${target.profile.project_id}/${isScenes ? "scenes" : "characters"}`}
                className="ff-quiet-button shrink-0"
              >
                在项目中打开
                <ArrowRight aria-hidden className="size-4" />
              </Link>
            </div>
          ) : (
            target.entry.source_text && (
              <div className="border-b border-border px-4 py-3">
                <p className="text-xs text-fg-subtle">参考描述</p>
                <p className="mt-1 text-sm leading-6 whitespace-pre-wrap text-fg-muted">{target.entry.source_text}</p>
              </div>
            )
          )}

          {isScenes ? (
            <ScenesView data={target.kind === "profile" ? target.profile.output : {}} />
          ) : (
            <CharactersView data={target.kind === "profile" ? target.profile.output : target.entry.output} />
          )}
        </>
      )}
    </Dialog>
  );
}
