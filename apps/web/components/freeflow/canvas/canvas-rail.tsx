"use client";

import type { FreeflowNodeType } from "@/lib/freeflow/types";

import { NODE_DRAG_MIME, NODE_TYPE_META, RAIL_FLOW_TYPES, RAIL_PRODUCTION_TYPES } from "./node-meta";

/**
 * 左侧图标栏（52px）。两种加节点的方式都支持：
 * 点一下 → 加到当前视口中心；拖到画布 → 加到落点。
 *
 * 下面一组是流程控制三项（REQ-023）。它们能拖进来、能连线，但 tooltip
 * 里写清楚「即将支持」——UI 上存在不等于跑得起来。
 */
export function CanvasRail({
  onAdd,
}: {
  onAdd: (type: FreeflowNodeType) => void;
}) {
  const item = (type: FreeflowNodeType, comingSoon: boolean) => {
    const meta = NODE_TYPE_META[type];
    const Icon = meta.icon;
    return (
      <button
        key={type}
        type="button"
        draggable
        onDragStart={(e) => {
          e.dataTransfer.setData(NODE_DRAG_MIME, type);
          e.dataTransfer.effectAllowed = "move";
        }}
        onClick={() => onAdd(type)}
        title={
          comingSoon
            ? `${meta.label}（即将支持：需要新的后端执行语义，REQ-023）— 点击或拖到画布`
            : `${meta.label} — 点击或拖到画布`
        }
        aria-label={`添加${meta.label}节点`}
        className="relative flex size-8 items-center justify-center rounded-lg text-fg-muted hover:bg-surface-2 hover:text-fg"
      >
        <Icon aria-hidden className="size-4" />
        {comingSoon && (
          <span
            aria-hidden
            className="absolute right-1 top-1 size-1.5 rounded-full bg-running"
          />
        )}
      </button>
    );
  };

  return (
    <nav
      aria-label="节点类型"
      className="flex w-13 shrink-0 flex-col items-center gap-1 border-r border-border bg-surface py-3"
    >
      {RAIL_PRODUCTION_TYPES.map((t) => item(t, false))}
      <div className="my-1 h-px w-6 bg-border" />
      {RAIL_FLOW_TYPES.map((t) => item(t, true))}
    </nav>
  );
}
