"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Images, Plus, Route } from "lucide-react";

import { projects, type Project } from "@/lib/api";
import { cn } from "@/lib/utils";

/** 剧本产出里的一集。只取渲染要用的两个字段。 */
export type EpisodeRef = { index: number; title: string };

const STATUS_LABEL: Record<string, string> = {
  draft: "草稿",
  routing: "路线判断",
  producing: "生产中",
  review: "待确认",
  completed: "已完成",
  archived: "已归档",
};

/**
 * 项目栏。
 *
 * 当前项目那张卡是展开的：路线、资源库入口、集数列表都挂在上面；
 * 其他项目只留一行，点了就切过去。
 *
 * 「集」在后端不是一等实体——它是剧本产出 `screenplay.episodes` 里的一项，
 * 没有独立的表也没有独立的接口。所以这里既不能新增也不能删除，选中一集
 * 只是**把中栏的剧本视图筛到这一集**，不改变任何生产范围。
 */
export function ProjectRail({
  projectId,
  current,
  episodes,
  selectedEpisode,
  onSelectEpisode,
  renderCount,
}: {
  projectId: string;
  current: Project | null;
  episodes: EpisodeRef[];
  selectedEpisode: number | null;
  onSelectEpisode: (index: number | null) => void;
  /** 这个项目已经出过的图张数，资源库磁贴上的数字 */
  renderCount: number;
}) {
  const [items, setItems] = useState<Project[]>([]);

  useEffect(() => {
    projects
      .list()
      .then((page) => setItems(page.items))
      .catch(() => setItems([]));
  }, []);

  const others = items.filter((p) => p.id !== projectId);

  return (
    <aside className="flex w-[236px] shrink-0 flex-col border-r border-border bg-bg">
      <div className="p-3">
        <Link
          href="/dashboard"
          className="flex h-8.5 w-full items-center justify-center gap-1.5 rounded-lg border border-border-strong bg-surface text-sm font-medium text-fg transition-colors duration-150 hover:bg-surface-2"
        >
          <Plus aria-hidden className="size-3.5" />
          新项目
        </Link>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
        <div className="rounded-xl border border-border bg-surface p-2.5">
          <div className="flex items-center gap-2.5">
            <div className="flex size-7.5 shrink-0 items-center justify-center rounded-lg bg-primary-soft text-sm font-semibold text-primary">
              {(current?.title ?? "…").trim().charAt(0)}
            </div>
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold">
                {current?.title ?? "加载中…"}
              </div>
              <div className="text-xs text-fg-subtle">
                {episodes.length > 0 ? `${episodes.length} 集 · ` : ""}
                {current ? (STATUS_LABEL[current.status] ?? current.status) : ""}
              </div>
            </div>
          </div>

          <div className="mt-2.5 grid grid-cols-2 gap-1.5">
            <Link
              href="/assets"
              className="rounded-lg border border-border bg-surface-2 px-1.5 py-2 text-center transition-colors duration-150 hover:bg-surface-3"
            >
              <Images aria-hidden className="mx-auto size-3.5 text-fg-muted" />
              <div className="mt-1 text-xs font-medium text-fg">资源库</div>
              <div className="tnum text-xs text-fg-subtle">{renderCount} 张</div>
            </Link>
            <div className="rounded-lg border border-border bg-surface-2 px-1.5 py-2 text-center">
              <Route aria-hidden className="mx-auto size-3.5 text-fg-muted" />
              <div className="mt-1 text-xs font-medium text-fg">路线</div>
              <div className="truncate text-xs text-fg-subtle">
                {current?.route_type ?? "未定"}
              </div>
            </div>
          </div>

          {episodes.length > 0 && (
            <>
              <div className="mt-3 mb-1.5 flex items-center justify-between">
                <span className="text-xs font-semibold tracking-wider text-fg-subtle">集</span>
                <button
                  type="button"
                  onClick={() => onSelectEpisode(null)}
                  className={cn(
                    "cursor-pointer rounded px-1 text-xs transition-colors duration-150",
                    selectedEpisode === null
                      ? "text-fg-subtle"
                      : "text-primary hover:underline",
                  )}
                  disabled={selectedEpisode === null}
                >
                  全部
                </button>
              </div>
              <ul className="flex flex-col gap-1">
                {episodes.map((ep) => {
                  const active = selectedEpisode === ep.index;
                  return (
                    <li key={ep.index}>
                      <button
                        type="button"
                        aria-pressed={active}
                        onClick={() => onSelectEpisode(active ? null : ep.index)}
                        className={cn(
                          "w-full cursor-pointer rounded-lg border px-2.5 py-1.5 text-left transition-colors duration-150",
                          active
                            ? "border-primary bg-primary-soft"
                            : "border-border bg-surface hover:bg-surface-2",
                        )}
                      >
                        <div
                          className={cn("text-xs", active ? "text-primary" : "text-fg-subtle")}
                        >
                          第 {ep.index} 集
                        </div>
                        <div
                          className={cn(
                            "truncate text-xs font-medium",
                            active ? "text-fg" : "text-fg-muted",
                          )}
                        >
                          {ep.title}
                        </div>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </div>

        {others.length > 0 && (
          <>
            <div className="px-1 pt-4 pb-1.5">
              <span className="text-xs font-semibold tracking-wider text-fg-subtle">
                其他项目
              </span>
            </div>
            <ul className="flex flex-col gap-1">
              {others.map((p) => (
                <li key={p.id}>
                  <Link
                    href={`/projects/${p.id}`}
                    className="flex items-center gap-2 rounded-lg px-2 py-1.5 transition-colors duration-150 hover:bg-surface-2"
                  >
                    <div className="flex size-6 shrink-0 items-center justify-center rounded-md bg-surface-2 text-xs font-semibold text-fg-muted">
                      {p.title.trim().charAt(0)}
                    </div>
                    <span className="min-w-0 flex-1 truncate text-xs text-fg-muted">
                      {p.title}
                    </span>
                    <span className="shrink-0 text-xs text-fg-subtle">
                      {STATUS_LABEL[p.status] ?? p.status}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </aside>
  );
}

/** 剧本产出 → 集列表。没跑出剧本就是空的。 */
export function episodesOf(screenplay: any): EpisodeRef[] {
  const raw = screenplay?.episodes;
  if (!Array.isArray(raw)) return [];
  return raw.map((ep: any) => ({
    index: Number(ep?.index ?? 0),
    title: String(ep?.title ?? ""),
  }));
}
