"use client";

import * as React from "react";
import { ChevronRight, ImageIcon, LayoutGrid, Loader2, Search, X } from "lucide-react";

import { MissingFrameIcon, StaleIcon } from "@/components/icons/studio-icons";
import {
  groupKey,
  isInFlight,
  shotCode,
  type DirectoryGroup,
  type ShotFact,
  type ShotFilter,
} from "@/lib/freeflow/storyboard-scope";
import { cn } from "@/lib/utils";

/** 主区正在看什么。镜头用数组位置，节点用 `groupKey`，`null` = 全部。 */
export type StoryboardView = { kind: "overview"; node: string | null } | { kind: "shot"; at: number };

export const FILTER_LABEL: Record<ShotFilter, string> = {
  all: "全部",
  missing: "缺图",
  failed: "失败",
  outdated: "待更新",
  running: "生成中",
};

const FILTER_HINT: Record<ShotFilter, string> = {
  all: "显示全部镜头",
  missing: "还没有首帧图的镜头",
  failed: "最近一次出图失败的镜头",
  outdated: "图出在镜头被改之前，可能对不上",
  running: "正在排队或生成的镜头",
};

export function groupTitle(group: DirectoryGroup): string {
  if (group.nodeIndex === null) return "未归属节点";
  return group.summary ? `节点 ${group.nodeIndex} · ${group.summary}` : `节点 ${group.nodeIndex}`;
}

/** 一镜在目录行上的状态：图标 + 读屏文字，不只靠颜色。 */
function FactGlyph({ fact }: { fact: ShotFact | undefined }) {
  if (isInFlight(fact)) {
    return (
      <span title="生成中" className="shrink-0 text-running">
        <Loader2 aria-hidden className="size-3.5 animate-spin" />
        <span className="sr-only">生成中</span>
      </span>
    );
  }
  if (fact?.status === "failed" && !fact.hasImage) {
    return (
      <span title="出图失败" className="shrink-0 text-danger">
        <X aria-hidden className="size-3.5" />
        <span className="sr-only">出图失败</span>
      </span>
    );
  }
  if (fact?.hasImage) {
    return (
      <span title={fact.outdated ? "有图，可能过期" : "已有首帧图"} className={cn("shrink-0", fact.outdated ? "text-rf-agent" : "text-success")}>
        {fact.outdated ? <StaleIcon aria-hidden className="size-3.5" /> : <ImageIcon aria-hidden className="size-3.5" />}
        <span className="sr-only">{fact.outdated ? "有图，可能过期" : "已有首帧图"}</span>
      </span>
    );
  }
  return (
    <span title="缺图" className="shrink-0 text-fg-subtle">
      <MissingFrameIcon aria-hidden className="size-3.5" />
      <span className="sr-only">缺图</span>
    </span>
  );
}

/**
 * 分镜目录：节点 → 镜头。
 *
 * 数据只来自真实的 `storyboard.nodes` 与 `shots[].node_index`，不猜集、不猜段。
 * 节点号不在 `nodes` 里的镜头进最后的「未归属节点」，所以任何一镜都找得到。
 *
 * 筛选与搜索只决定显示哪几行；点一行传回去的是**数组位置**，与筛选无关。
 */
export function StoryboardDirectory({
  groups,
  shots,
  facts,
  visible,
  query,
  onQuery,
  filter,
  onFilter,
  filterCounts,
  view,
  onOverview,
  onNode,
  onShot,
  totalShots,
}: {
  groups: DirectoryGroup[];
  shots: { index: number; content?: string | null }[];
  facts: ShotFact[];
  /** 通过筛选与搜索的数组位置 */
  visible: ReadonlySet<number>;
  query: string;
  onQuery: (q: string) => void;
  filter: ShotFilter;
  onFilter: (f: ShotFilter) => void;
  filterCounts: Record<ShotFilter, number>;
  view: StoryboardView;
  onOverview: () => void;
  onNode: (key: string) => void;
  onShot: (at: number) => void;
  totalShots: number;
}) {
  const searchId = React.useId();
  const narrowing = filter !== "all" || query.trim() !== "";

  const activeGroupKey = React.useMemo(() => {
    if (view.kind === "overview") return view.node;
    const g = groups.find((group) => group.positions.includes(view.at));
    return g ? groupKey(g.nodeIndex) : null;
  }, [view, groups]);

  const [expanded, setExpanded] = React.useState<Set<string>>(() => new Set());
  // 选中的镜头或节点所在的组自动展开；用户自己收起的其他组不动
  React.useEffect(() => {
    if (!activeGroupKey) return;
    setExpanded((prev) => (prev.has(activeGroupKey) ? prev : new Set(prev).add(activeGroupKey)));
  }, [activeGroupKey]);

  const toggle = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const shownGroups = groups
    .map((group) => ({ group, hits: group.positions.filter((at) => visible.has(at)) }))
    // 在筛选时把一个命中都没有的节点藏掉；不筛时空节点也列出来，覆盖不全要看得见
    .filter(({ hits }) => !narrowing || hits.length > 0);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 border-b border-border px-3 pt-3 pb-2.5">
        <div className="flex items-baseline justify-between gap-2 px-1">
          <h2 className="ff-display text-lg text-fg">分镜</h2>
          <span className="tnum text-xs text-fg-subtle">
            {totalShots} 镜 · {groups.filter((g) => g.nodeIndex !== null).length} 节点
          </span>
        </div>

        <label htmlFor={searchId} className="sr-only">
          搜索镜头
        </label>
        <div className="relative mt-2">
          <Search aria-hidden className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-fg-subtle" />
          <input
            id={searchId}
            type="search"
            value={query}
            onChange={(e) => onQuery(e.target.value)}
            placeholder="镜号、画面、台词、场景"
            className="h-8 w-full rounded-md border border-border bg-bg pr-2 pl-8 text-[13px] text-fg placeholder:text-fg-subtle focus:border-primary focus:outline-none"
          />
        </div>

        <div role="group" aria-label="按状态筛选" className="mt-2 flex flex-wrap gap-1">
          {(Object.keys(FILTER_LABEL) as ShotFilter[]).map((key) => {
            const on = filter === key;
            const count = filterCounts[key];
            return (
              <button
                key={key}
                type="button"
                aria-pressed={on}
                title={FILTER_HINT[key]}
                disabled={key !== "all" && count === 0 && !on}
                onClick={() => onFilter(key)}
                className={cn(
                  "tnum inline-flex h-7 items-center gap-1 rounded-md border px-2 text-xs transition-colors duration-150",
                  "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-primary",
                  "disabled:cursor-not-allowed disabled:opacity-40",
                  on
                    ? "border-primary/60 bg-primary-soft text-primary"
                    : "border-border text-fg-muted hover:border-border-strong hover:text-fg",
                )}
              >
                {FILTER_LABEL[key]}
                <span className={on ? "text-primary" : "text-fg-subtle"}>{count}</span>
              </button>
            );
          })}
        </div>
      </div>

      <nav aria-label="分镜目录" className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
        <button
          type="button"
          onClick={onOverview}
          aria-current={view.kind === "overview" && view.node === null ? "true" : undefined}
          className={cn(
            "flex h-9 w-full items-center gap-2 rounded-md px-2 text-left text-sm",
            "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary",
            view.kind === "overview" && view.node === null
              ? "bg-primary-soft font-medium text-primary"
              : "text-fg hover:bg-surface-2",
          )}
        >
          <LayoutGrid aria-hidden className="size-4 shrink-0" />
          <span className="flex-1">总览</span>
          <span className="tnum text-xs text-fg-subtle">
            {narrowing ? `${visible.size} / ${totalShots}` : totalShots}
          </span>
        </button>

        {shownGroups.length === 0 && (
          <p className="px-2 py-6 text-center text-xs leading-5 text-fg-subtle">
            没有符合条件的镜头。换个关键词，或把筛选切回「全部」。
          </p>
        )}

        <ul className="mt-1 flex flex-col gap-0.5">
          {shownGroups.map(({ group, hits }) => {
            const key = groupKey(group.nodeIndex);
            const open = expanded.has(key) || (narrowing && hits.length > 0 && query.trim() !== "");
            const isCurrentNode = view.kind === "overview" && view.node === key;
            const missing = group.positions.filter((at) => !facts[at]?.hasImage).length;
            const listId = `sb-group-${key}`;
            return (
              <li key={key}>
                <div
                  className={cn(
                    "flex items-center rounded-md",
                    isCurrentNode ? "bg-primary-soft text-primary" : "text-fg hover:bg-surface-2",
                  )}
                >
                  <button
                    type="button"
                    aria-expanded={open}
                    aria-controls={listId}
                    aria-label={`${open ? "收起" : "展开"}${groupTitle(group)}`}
                    onClick={() => toggle(key)}
                    className="grid size-8 shrink-0 place-items-center rounded-md text-fg-subtle hover:text-fg focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary"
                  >
                    <ChevronRight aria-hidden className={cn("size-3.5 transition-transform duration-150 motion-reduce:transition-none", open && "rotate-90")} />
                  </button>
                  <button
                    type="button"
                    onClick={() => onNode(key)}
                    aria-current={isCurrentNode ? "true" : undefined}
                    title={groupTitle(group)}
                    className="flex min-h-8 min-w-0 flex-1 items-center gap-2 py-1 pr-2 text-left text-[13px] focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary"
                  >
                    <span className={cn("min-w-0 flex-1 truncate", group.nodeIndex === null && "text-fg-muted italic")}>
                      {groupTitle(group)}
                    </span>
                    <span className="tnum shrink-0 text-xs text-fg-subtle">
                      {group.positions.length === 0
                        ? "无镜头"
                        : `${narrowing ? `${hits.length}/` : ""}${group.positions.length} 镜${missing > 0 ? ` · 缺 ${missing}` : ""}`}
                    </span>
                  </button>
                </div>

                {open && hits.length > 0 && (
                  <ul id={listId} className="mt-0.5 mb-1 ml-4 flex flex-col gap-px border-l border-border pl-1.5">
                    {hits.map((at) => {
                      const shot = shots[at];
                      if (!shot) return null;
                      const current = view.kind === "shot" && view.at === at;
                      const text = String(shot.content ?? "").trim() || "（画面内容为空）";
                      return (
                        <li key={at}>
                          <button
                            type="button"
                            onClick={() => onShot(at)}
                            aria-current={current ? "true" : undefined}
                            className={cn(
                              "flex h-8 w-full items-center gap-2 rounded-md px-2 text-left text-[13px]",
                              "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary",
                              current ? "bg-surface-3 text-fg shadow-[inset_2px_0_0_var(--primary)]" : "text-fg-muted hover:bg-surface-2 hover:text-fg",
                            )}
                          >
                            <span className="ff-shot-no shrink-0 text-fg-subtle">{shotCode(shot.index)}</span>
                            <span className="min-w-0 flex-1 truncate">{text}</span>
                            <FactGlyph fact={facts[at]} />
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
