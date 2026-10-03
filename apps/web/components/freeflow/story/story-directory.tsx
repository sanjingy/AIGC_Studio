"use client";

import * as React from "react";
import { BookOpen, ChevronRight, FileText, Layers, Pencil, Search } from "lucide-react";

import {
  episodeLabel,
  sceneKey,
  type EpisodeGroup,
  type ScenePos,
  type StoryView,
} from "@/lib/freeflow/screenplay-scope";
import { cn } from "@/lib/utils";

/**
 * 故事 / 剧本目录：故事 → 完整剧本 → 集 → 场。
 *
 * 数据只来自真实的 `screenplay.episodes[].scenes[]`，不猜集号、不猜时长。
 * 行上显示的是**展示编号**（集号 `index`、场号 `id`），点一行传回去的是
 * **数组位置**（`{ep, at}`）——两者分开是本轮的核心约束：集号可以不连续，
 * 场号跨集可以重复。
 *
 * 搜索只决定显示哪几行，不导航、不改身份，所以不可能丢掉正在编辑的草稿；
 * 正在编辑的那一场由 `visibleScenes(..., pinned)` 钉在结果里，即使被筛掉。
 */
export function StoryDirectory({
  groups,
  visible,
  query,
  onQuery,
  totalScenes,
  view,
  dirtyScene,
  hasPlotIndex,
  hasScreenplay,
  onStory,
  onScript,
  onEpisode,
  onScene,
}: {
  groups: EpisodeGroup[];
  /** 通过搜索的场，`sceneKey` 串 */
  visible: ReadonlySet<string>;
  query: string;
  onQuery: (q: string) => void;
  totalScenes: number;
  view: StoryView;
  /** 有未保存草稿的那一场；用来在目录上标出来 */
  dirtyScene: ScenePos | null;
  hasPlotIndex: boolean;
  hasScreenplay: boolean;
  onStory: () => void;
  onScript: () => void;
  onEpisode: (at: number) => void;
  onScene: (pos: ScenePos) => void;
}) {
  const searchId = React.useId();
  const narrowing = query.trim() !== "";

  const activeEpisode =
    view.kind === "episode" ? view.at : view.kind === "scene" ? view.ep : null;

  const [expanded, setExpanded] = React.useState<Set<number>>(() => new Set());
  // 选中的集 / 场所在的集自动展开；用户自己收起的其他集不动
  React.useEffect(() => {
    if (activeEpisode === null) return;
    setExpanded((prev) => (prev.has(activeEpisode) ? prev : new Set(prev).add(activeEpisode)));
  }, [activeEpisode]);

  const toggle = (at: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(at)) next.delete(at);
      else next.add(at);
      return next;
    });

  const shown = groups
    .map((group) => ({
      group,
      hits: group.scenes.filter((row) => visible.has(sceneKey({ ep: group.at, at: row.at }))),
    }))
    // 搜索时把一个命中都没有的集藏掉；不搜时全部列出，空集也要看得见
    .filter(({ hits }) => !narrowing || hits.length > 0);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 border-b border-border px-3 pt-3 pb-2.5">
        <div className="flex items-baseline justify-between gap-2 px-1">
          <h2 className="ff-display text-lg text-fg">剧本</h2>
          <span className="tnum text-xs text-fg-subtle">
            {groups.length} 集 · {totalScenes} 场
          </span>
        </div>

        <label htmlFor={searchId} className="sr-only">
          搜索场次、地点或台词
        </label>
        <div className="relative mt-2">
          <Search
            aria-hidden
            className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-fg-subtle"
          />
          <input
            id={searchId}
            type="search"
            value={query}
            onChange={(e) => onQuery(e.target.value)}
            placeholder="场号、地点、角色、台词"
            disabled={!hasScreenplay}
            className="h-8 w-full rounded-md border border-border bg-bg pr-2 pl-8 text-[13px] text-fg placeholder:text-fg-subtle focus:border-primary focus:outline-none disabled:opacity-50"
          />
        </div>
        {narrowing && (
          <p className="tnum mt-1.5 px-1 text-xs text-fg-subtle">
            命中 {visible.size} / {totalScenes} 场
          </p>
        )}
      </div>

      <nav aria-label="故事与剧本目录" className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
        <TopRow
          icon={BookOpen}
          label="故事 · 情节目录"
          hint={hasPlotIndex ? undefined : "还没有情节目录"}
          current={view.kind === "story"}
          onClick={onStory}
        />
        <TopRow
          icon={FileText}
          label="完整剧本"
          count={hasScreenplay ? totalScenes : undefined}
          hint={hasScreenplay ? undefined : "还没有剧本"}
          current={view.kind === "script"}
          onClick={onScript}
        />

        {hasScreenplay && shown.length === 0 && (
          <p className="px-2 py-6 text-center text-xs leading-5 text-fg-subtle">
            没有符合搜索的场次。换个关键词，或清空搜索框。
          </p>
        )}

        <ul className="mt-1 flex flex-col gap-0.5">
          {shown.map(({ group, hits }) => {
            const open = expanded.has(group.at) || (narrowing && hits.length > 0);
            const isCurrent = view.kind === "episode" && view.at === group.at;
            const listId = `story-ep-${group.at}`;
            const title = group.title || "未命名";
            return (
              <li key={group.at}>
                <div
                  className={cn(
                    "flex items-center rounded-md",
                    isCurrent ? "bg-primary-soft text-primary" : "text-fg hover:bg-surface-2",
                  )}
                >
                  <button
                    type="button"
                    aria-expanded={open}
                    aria-controls={listId}
                    aria-label={`${open ? "收起" : "展开"}${episodeLabel(group)}`}
                    onClick={() => toggle(group.at)}
                    className="grid size-8 shrink-0 place-items-center rounded-md text-fg-subtle hover:text-fg focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary"
                  >
                    <ChevronRight
                      aria-hidden
                      className={cn(
                        "size-3.5 transition-transform duration-150 motion-reduce:transition-none",
                        open && "rotate-90",
                      )}
                    />
                  </button>
                  <button
                    type="button"
                    onClick={() => onEpisode(group.at)}
                    aria-current={isCurrent ? "true" : undefined}
                    title={`${episodeLabel(group)} ${title}`}
                    className="flex min-h-8 min-w-0 flex-1 items-center gap-2 py-1 pr-2 text-left text-[13px] focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary"
                  >
                    <span className="tnum shrink-0 text-fg-subtle">
                      {group.index === null ? `#${group.at + 1}` : `E${group.index}`}
                    </span>
                    <span className="min-w-0 flex-1 truncate">{title}</span>
                    <span className="tnum shrink-0 text-xs text-fg-subtle">
                      {group.scenes.length === 0
                        ? "无场次"
                        : `${narrowing ? `${hits.length}/` : ""}${group.scenes.length} 场`}
                    </span>
                  </button>
                </div>

                {open && hits.length > 0 && (
                  <ul
                    id={listId}
                    className="mt-0.5 mb-1 ml-4 flex flex-col gap-px border-l border-border pl-1.5"
                  >
                    {hits.map((row) => {
                      const pos = { ep: group.at, at: row.at };
                      const current =
                        view.kind === "scene" && view.ep === pos.ep && view.at === pos.at;
                      const dirty =
                        dirtyScene !== null &&
                        dirtyScene.ep === pos.ep &&
                        dirtyScene.at === pos.at;
                      const label = row.location || row.timeMood || "（未填地点）";
                      return (
                        <li key={row.at}>
                          <button
                            type="button"
                            onClick={() => onScene(pos)}
                            aria-current={current ? "true" : undefined}
                            title={`${row.id || `第 ${row.at + 1} 场`} ${label}`}
                            className={cn(
                              "flex h-8 w-full items-center gap-2 rounded-md px-2 text-left text-[13px]",
                              "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary",
                              current
                                ? "bg-surface-3 text-fg shadow-[inset_2px_0_0_var(--primary)]"
                                : "text-fg-muted hover:bg-surface-2 hover:text-fg",
                            )}
                          >
                            <span className="tnum shrink-0 text-fg-subtle">
                              {row.id || `#${row.at + 1}`}
                            </span>
                            <span className="min-w-0 flex-1 truncate">{label}</span>
                            {dirty ? (
                              <span title="有未保存的改动" className="shrink-0 text-rf-warning">
                                <Pencil aria-hidden className="size-3" />
                                <span className="sr-only">有未保存的改动</span>
                              </span>
                            ) : (
                              <span className="tnum shrink-0 text-xs text-fg-subtle">
                                {row.beatCount}
                              </span>
                            )}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      </nav>
    </div>
  );
}

function TopRow({
  icon: Icon,
  label,
  count,
  hint,
  current,
  onClick,
}: {
  icon: typeof Layers;
  label: string;
  count?: number;
  hint?: string;
  current: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={current ? "true" : undefined}
      className={cn(
        "flex min-h-9 w-full items-center gap-2 rounded-md px-2 text-left text-sm",
        "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary",
        current ? "bg-primary-soft font-medium text-primary" : "text-fg hover:bg-surface-2",
      )}
    >
      <Icon aria-hidden className="size-4 shrink-0" />
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {count !== undefined && <span className="tnum text-xs text-fg-subtle">{count}</span>}
      {hint && <span className="shrink-0 text-xs text-fg-subtle">{hint}</span>}
    </button>
  );
}
