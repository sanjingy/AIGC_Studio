"use client";

import * as React from "react";
import { AlertCircle, Clock3, ImageIcon, LayoutGrid, Search } from "lucide-react";

import { ShotImage } from "@/components/freeflow/storyboard/shot-image";
import type { ProfileRow, ProfileView } from "@/lib/freeflow/profile-scope";
import { cn } from "@/lib/utils";

/** 一个对象的出图状态，目录、总览、计数三处共用一份判定 */
export type ImageState = "ready" | "outdated" | "running" | "failed" | "missing";

export const IMAGE_STATE_LABEL: Record<ImageState, string> = {
  ready: "已有图",
  outdated: "图可能过期",
  running: "生成中",
  failed: "上次失败",
  missing: "未出图",
};

const TONE: Record<ImageState, string> = {
  ready: "text-success",
  outdated: "text-rf-agent",
  running: "text-running",
  failed: "text-danger",
  missing: "text-fg-subtle",
};

function StateIcon({ state }: { state: ImageState }) {
  const Icon = state === "failed" ? AlertCircle : state === "running" ? Clock3 : ImageIcon;
  return (
    <span title={IMAGE_STATE_LABEL[state]} className={cn("shrink-0", TONE[state])}>
      <Icon aria-hidden className={cn("size-3.5", state === "missing" && "opacity-50")} />
      <span className="sr-only">{IMAGE_STATE_LABEL[state]}</span>
    </span>
  );
}

/**
 * 对象目录。搜索只决定显示——不导航，所以不会丢掉正在编辑的草稿；
 * 正在编辑的那一个由调用方钉进 `visible`，这里再标一个「未保存」。
 */
export function ProfileDirectory({
  heading,
  noun,
  rows,
  visible,
  hitCount,
  states,
  query,
  onQuery,
  view,
  dirtyAt,
  onOverview,
  onItem,
  lockedReason = null,
}: {
  heading: string;
  /** 「角色」「场景」 */
  noun: string;
  rows: ProfileRow[];
  visible: number[];
  /** 真正匹配搜索的个数（不含被钉住的正在编辑的那一个） */
  hitCount: number;
  states: ImageState[];
  query: string;
  onQuery: (q: string) => void;
  view: ProfileView;
  dirtyAt: number | null;
  onOverview: () => void;
  onItem: (at: number) => void;
  /** 正在写库时不许切对象：保存结果要落回发起它的那个对象。不为空时目录按钮全部禁用并显示原因 */
  lockedReason?: string | null;
}) {
  const locked = Boolean(lockedReason);
  const searchId = React.useId();
  const narrowing = query.trim() !== "";
  const withImage = states.filter((s) => s === "ready" || s === "outdated").length;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 border-b border-border px-3 pt-3 pb-2.5">
        <div className="flex items-baseline justify-between gap-2 px-1">
          <h2 className="ff-display text-lg text-fg">{heading}</h2>
          <span className="tnum text-xs text-fg-subtle">
            {rows.length} 个 · 有图 {withImage}
          </span>
        </div>
        <label htmlFor={searchId} className="sr-only">
          搜索{noun}
        </label>
        <div className="relative mt-2">
          <Search aria-hidden className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-fg-subtle" />
          <input
            id={searchId}
            type="search"
            value={query}
            onChange={(e) => onQuery(e.target.value)}
            placeholder={noun === "角色" ? "名称、身份、ref" : "名称、时段、环境、ref"}
            disabled={rows.length === 0}
            className="h-8 w-full rounded-md border border-border bg-bg pr-2 pl-8 text-[13px] text-fg placeholder:text-fg-subtle focus:border-primary focus:outline-none disabled:opacity-50"
          />
        </div>
        {narrowing && (
          <p className="tnum mt-1.5 px-1 text-xs text-fg-subtle">
            命中 {hitCount} / {rows.length} 个
          </p>
        )}
        {locked && (
          <p role="status" className="mt-1.5 px-1 text-xs text-running">
            {lockedReason}
          </p>
        )}
      </div>

      <nav aria-label={`${heading}目录`} className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
        <button
          type="button"
          onClick={onOverview}
          disabled={locked}
          aria-current={view.kind === "overview" ? "true" : undefined}
          className={cn(
            "flex min-h-8 w-full items-center gap-2 rounded-md px-2 py-1 text-left text-[13px] focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary disabled:cursor-not-allowed disabled:opacity-60",
            view.kind === "overview" ? "bg-primary-soft text-primary" : "text-fg hover:bg-surface-2",
          )}
        >
          <LayoutGrid aria-hidden className="size-3.5 shrink-0" />
          <span className="flex-1">全部{noun}（总览）</span>
          <span className="tnum text-xs text-fg-subtle">{rows.length}</span>
        </button>

        {narrowing && hitCount === 0 && (
          <p className="px-2 py-6 text-center text-xs leading-5 text-fg-subtle">没有符合搜索的{noun}。换个关键词，或清空搜索框。</p>
        )}

        <ul className="mt-1 flex flex-col gap-px">
          {visible.map((at) => {
            const row = rows[at]!;
            const current = view.kind === "item" && view.at === at;
            const dirty = dirtyAt === at;
            return (
              <li key={at}>
                <button
                  type="button"
                  onClick={() => onItem(at)}
                  disabled={locked && !current}
                  aria-current={current ? "true" : undefined}
                  title={`${row.name || "未命名"} ${row.ref}`}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary disabled:cursor-not-allowed disabled:opacity-60",
                    current ? "bg-primary-soft" : "hover:bg-surface-2",
                  )}
                >
                  <span className="min-w-0 flex-1">
                    <span className="flex items-baseline gap-1.5">
                      <span className={cn("truncate text-[13px]", current ? "text-primary" : "text-fg")}>{row.name || "未命名"}</span>
                      <span className="shrink-0 text-[11px] text-fg-subtle">{row.ref}</span>
                    </span>
                    {row.subtitle && <span className="block truncate text-xs text-fg-subtle">{row.subtitle}</span>}
                  </span>
                  {dirty && <span className="shrink-0 text-[11px] text-rf-warning">未保存</span>}
                  <StateIcon state={states[at] ?? "missing"} />
                </button>
              </li>
            );
          })}
        </ul>
      </nav>
    </div>
  );
}

/** 总览里的一张卡：缩略图 + 名称 + 状态。点它进入这个对象。 */
export function ProfileCard({
  row,
  state,
  src,
  square,
  meta,
  onOpen,
}: {
  row: ProfileRow;
  state: ImageState;
  src: string | undefined;
  square: boolean;
  meta?: string;
  onOpen: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        className="flex w-full flex-col overflow-hidden rounded-[2px] border border-border bg-surface text-left hover:border-border-strong focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-primary"
      >
        <span className={cn("block w-full bg-surface-2", square ? "aspect-square" : "aspect-[3/4]")}>
          <ShotImage src={src} alt="" className="object-contain" noun={square ? "四视图参考图" : "基准立绘"} />
        </span>
        <span className="flex flex-col gap-0.5 px-2.5 py-2">
          <span className="flex items-baseline gap-1.5">
            <span className="truncate text-[13px] font-medium text-fg">{row.name || "未命名"}</span>
            <span className="shrink-0 text-[11px] text-fg-subtle">{row.ref}</span>
          </span>
          {(meta || row.subtitle) && <span className="line-clamp-2 text-xs text-fg-subtle">{meta || row.subtitle}</span>}
          <span className={cn("text-xs", TONE[state])}>{IMAGE_STATE_LABEL[state]}</span>
        </span>
      </button>
    </li>
  );
}
