"use client";

import * as React from "react";

import { episodeLabel, type EpisodeGroup, type SceneRow } from "@/lib/freeflow/screenplay-scope";
import { cn } from "@/lib/utils";

/**
 * 集表与场表。两张都是**可点的接触表**：一行 = 一个真实对象，点进去编辑。
 *
 * 显示的是展示编号（集号 `index`、场号 `id`），回调传的是数组位置
 * （`group.at` / `row.at`）——写路径只认数组位置。
 *
 * 表里不出现时长、秒数、视频状态：后端没有这些字段。
 */

export type SceneCoverage = {
  /** 落在这一场上的情节节点号 */
  nodes: number[];
  /** 场号在多集重复，覆盖归属无法确定 */
  ambiguous: boolean;
};

function CoverageCell({ coverage }: { coverage: SceneCoverage }) {
  if (coverage.ambiguous) {
    return (
      <span title="这个场号在多集里重复，无法确定 node_coverage 指的是哪一场" className="text-rf-warning">
        场号重复
      </span>
    );
  }
  if (coverage.nodes.length === 0) return <span className="text-fg-subtle">—</span>;
  return <span className="tnum">{coverage.nodes.join("、")}</span>;
}

export function EpisodeTable({
  groups,
  coverageOf,
  onEpisode,
}: {
  groups: EpisodeGroup[];
  coverageOf: (group: EpisodeGroup) => SceneCoverage;
  onEpisode: (at: number) => void;
}) {
  if (groups.length === 0) {
    return (
      <p className="rounded-[2px] border border-dashed border-border px-4 py-8 text-center text-xs text-fg-subtle">
        这份剧本里没有集。只能返工整段剧本来补。
      </p>
    );
  }
  return (
    <div className="overflow-x-auto rounded-[2px] border border-border">
      <table className="w-full min-w-[40rem] border-collapse text-[13px]">
        <caption className="sr-only">分集一览，点一行进入那一集</caption>
        <thead>
          <tr className="border-b border-border bg-surface-2 text-left text-xs text-fg-subtle">
            <th scope="col" className="px-3 py-2 font-medium">集</th>
            <th scope="col" className="px-3 py-2 font-medium">标题</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">场</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">节拍</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">对白/旁白</th>
            <th scope="col" className="px-3 py-2 font-medium">覆盖情节节点</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((group) => (
            <tr
              key={group.at}
              tabIndex={0}
              role="button"
              onClick={() => onEpisode(group.at)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onEpisode(group.at);
                }
              }}
              className="cursor-pointer border-b border-border last:border-0 hover:bg-surface-2 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary"
            >
              <td className="tnum px-3 py-2 text-fg">{episodeLabel(group)}</td>
              <td className="max-w-[18rem] truncate px-3 py-2 text-fg-muted">
                {group.title || "未命名"}
              </td>
              <td className="tnum px-3 py-2 text-right text-fg-muted">{group.scenes.length}</td>
              <td className="tnum px-3 py-2 text-right text-fg-muted">{group.beatCount}</td>
              <td className="tnum px-3 py-2 text-right text-fg-muted">{group.dialogueCount}</td>
              <td className="px-3 py-2 text-xs text-fg-muted">
                <CoverageCell coverage={coverageOf(group)} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SceneTable({
  rows,
  visible,
  coverageOf,
  dirtyAt,
  onScene,
  emptyText,
}: {
  rows: SceneRow[];
  /** 通过搜索的场在该集里的数组位置；`null` = 不筛 */
  visible: ReadonlySet<number> | null;
  coverageOf: (row: SceneRow) => SceneCoverage;
  /** 有未保存草稿的那一场 */
  dirtyAt: number | null;
  onScene: (at: number) => void;
  emptyText: string;
}) {
  const shown = visible ? rows.filter((row) => visible.has(row.at)) : rows;
  if (shown.length === 0) {
    return (
      <p className="rounded-[2px] border border-dashed border-border px-4 py-8 text-center text-xs text-fg-subtle">
        {emptyText}
      </p>
    );
  }
  return (
    <div className="overflow-x-auto rounded-[2px] border border-border">
      <table className="w-full min-w-[44rem] border-collapse text-[13px]">
        <caption className="sr-only">场次一览，点一行进入那一场</caption>
        <thead>
          <tr className="border-b border-border bg-surface-2 text-left text-xs text-fg-subtle">
            <th scope="col" className="px-3 py-2 font-medium">场号</th>
            <th scope="col" className="px-3 py-2 font-medium">地点</th>
            <th scope="col" className="px-3 py-2 font-medium">时间与氛围</th>
            <th scope="col" className="px-3 py-2 font-medium">出场</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">节拍</th>
            <th scope="col" className="px-3 py-2 font-medium">覆盖节点</th>
            <th scope="col" className="px-3 py-2 font-medium">钩子</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((row) => (
            <tr
              key={row.at}
              tabIndex={0}
              role="button"
              onClick={() => onScene(row.at)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onScene(row.at);
                }
              }}
              className={cn(
                "cursor-pointer border-b border-border last:border-0 hover:bg-surface-2",
                "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary",
                dirtyAt === row.at && "bg-rf-warning-soft",
              )}
            >
              <td className="tnum px-3 py-2 text-fg">
                {row.id || `#${row.at + 1}`}
                {dirtyAt === row.at && (
                  <span className="ml-1.5 text-[11px] text-rf-warning">未保存</span>
                )}
              </td>
              <td className="max-w-[12rem] truncate px-3 py-2 text-fg-muted">
                {row.location || "—"}
              </td>
              <td className="max-w-[10rem] truncate px-3 py-2 text-fg-muted">
                {row.timeMood || "—"}
              </td>
              <td className="max-w-[12rem] truncate px-3 py-2 text-xs text-fg-muted">
                {row.characterRefs.length > 0 ? row.characterRefs.join("、") : "—"}
              </td>
              <td className="tnum px-3 py-2 text-right text-fg-muted">{row.beatCount}</td>
              <td className="px-3 py-2 text-xs text-fg-muted">
                <CoverageCell coverage={coverageOf(row)} />
              </td>
              <td className="max-w-[14rem] truncate px-3 py-2 text-xs text-fg-muted">
                {row.hook || "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
