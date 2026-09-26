"use client";

import * as React from "react";

import { groupKey, type DirectoryGroup, type ShotFact, type Tally } from "@/lib/freeflow/storyboard-scope";
import { cn } from "@/lib/utils";

import { ShotGrid, type ShotCardData } from "./shot-card";
import { groupTitle } from "./storyboard-directory";

const STATS: { key: keyof Tally; label: string; tone?: string }[] = [
  { key: "total", label: "镜头" },
  { key: "withImage", label: "已出图", tone: "text-success" },
  { key: "missing", label: "缺图" },
  { key: "failed", label: "失败", tone: "text-danger" },
  { key: "running", label: "生成中", tone: "text-running" },
  { key: "outdated", label: "待更新", tone: "text-rf-agent" },
];

/** 统计一行：只放后端真的有的数。没有时长、没有集。 */
export function StatStrip({ tally, label }: { tally: Tally; label: string }) {
  return (
    <dl aria-label={label} className="grid grid-cols-3 overflow-hidden rounded-md border border-border sm:grid-cols-6">
      {STATS.map(({ key, label: name, tone }) => {
        const value = tally[key];
        return (
          <div key={key} className="border-border px-3 py-2 not-last:border-r max-sm:[&:nth-child(3)]:border-r-0 max-sm:[&:nth-child(-n+3)]:border-b">
            <dt className="text-xs text-fg-muted">{name}</dt>
            <dd className={cn("tnum mt-0.5 text-lg font-semibold", value > 0 && tone ? tone : "text-fg")}>{value}</dd>
          </div>
        );
      })}
    </dl>
  );
}

/**
 * 节点覆盖表：每个真实节点下有几镜、几镜有图、缺几、失败几。
 * 点一行等于在目录里选中这个节点。没有镜头的节点标出来——那是覆盖缺口。
 */
export function NodeCoverage({
  groups,
  facts,
  sceneName,
  onNode,
}: {
  groups: DirectoryGroup[];
  facts: ShotFact[];
  sceneName: (ref: string) => string | undefined;
  onNode: (key: string) => void;
}) {
  if (groups.length === 0) return null;
  return (
    <section aria-labelledby="sb-coverage">
      <h3 id="sb-coverage" className="text-sm font-semibold text-fg">
        节点覆盖
      </h3>
      <div className="mt-2 overflow-x-auto rounded-md border border-border">
        <table className="w-full min-w-[560px] border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-border bg-surface text-left text-xs text-fg-muted">
              <th scope="col" className="px-3 py-2 font-normal">节点</th>
              <th scope="col" className="px-3 py-2 font-normal">场景</th>
              <th scope="col" className="px-3 py-2 text-right font-normal">镜</th>
              <th scope="col" className="px-3 py-2 text-right font-normal">已出图</th>
              <th scope="col" className="px-3 py-2 text-right font-normal">缺图</th>
              <th scope="col" className="px-3 py-2 text-right font-normal">失败</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((group) => {
              const key = groupKey(group.nodeIndex);
              const withImage = group.positions.filter((at) => facts[at]?.hasImage).length;
              const failed = group.positions.filter((at) => facts[at]?.status === "failed").length;
              const missing = group.positions.length - withImage;
              const empty = group.positions.length === 0;
              return (
                <tr key={key} className="border-b border-border last:border-b-0 hover:bg-surface">
                  <th scope="row" className="max-w-[18rem] px-3 py-1.5 text-left font-normal">
                    <button
                      type="button"
                      onClick={() => onNode(key)}
                      className="block w-full truncate text-left text-fg hover:text-primary focus-visible:outline-2 focus-visible:outline-primary"
                      title={groupTitle(group)}
                    >
                      {groupTitle(group)}
                    </button>
                  </th>
                  <td className="max-w-[10rem] truncate px-3 py-1.5 text-fg-muted" title={group.sceneRef}>
                    {group.sceneRef ? (sceneName(group.sceneRef) ?? group.sceneRef) : "—"}
                  </td>
                  <td className="tnum px-3 py-1.5 text-right text-fg">
                    {empty ? <span className="text-running">无镜头</span> : group.positions.length}
                  </td>
                  <td className="tnum px-3 py-1.5 text-right text-fg-muted">{withImage}</td>
                  <td className={cn("tnum px-3 py-1.5 text-right", missing > 0 ? "text-fg" : "text-fg-subtle")}>{missing}</td>
                  <td className={cn("tnum px-3 py-1.5 text-right", failed > 0 ? "text-danger" : "text-fg-subtle")}>{failed}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** 当前范围（筛选后）的镜头接触表。点一张进编辑视图，传回数组位置。 */
export function ScopeSheet({
  positions,
  cards,
  onShot,
  emptyText,
}: {
  positions: number[];
  cards: ShotCardData[];
  onShot: (at: number) => void;
  emptyText: string;
}) {
  const list = positions.map((at) => cards[at]).filter((c): c is ShotCardData => Boolean(c));
  if (list.length === 0) {
    return <p className="rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-fg-subtle">{emptyText}</p>;
  }
  return <ShotGrid shots={list} selectedIndex={null} onSelect={(i) => onShot(positions[i]!)} />;
}
