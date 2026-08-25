"use client";

import { useState } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { ChevronDown, ChevronRight, Hourglass } from "lucide-react";

import { NODE_TYPES_NEEDING_BACKEND } from "@/lib/freeflow/types";
import { cn } from "@/lib/utils";

import type { CanvasNode } from "./graph";
import { INLINE_PARAM_LIMIT, NODE_TYPE_META, STATUS_META } from "./node-meta";

/** 锚点视觉：颜色一律走 token，不写死。inline style 是为了压过 React Flow
 *  自带样式表里的 `.react-flow__handle` 默认色，避免依赖 CSS 引入顺序。 */
const HANDLE_STYLE = {
  width: 9,
  height: 9,
  background: "var(--surface)",
  border: "1.5px solid var(--border-strong)",
} as const;

/**
 * 节点卡片（REQ-020）：顶部一条状态色条 + 标题 + 最多 4 个内联参数 +
 * 状态文字。多出的参数收进「更多设置」折叠区，**不弹窗**——避免 ComfyUI
 * 式的参数弹窗堆叠，这是需求原文点名的取舍。
 */
export function NodeCard({ data, selected }: NodeProps<CanvasNode>) {
  const [expanded, setExpanded] = useState(false);
  const meta = NODE_TYPE_META[data.nodeType];
  const status = STATUS_META[data.status];
  const Icon = meta.icon;
  const needsBackend = NODE_TYPES_NEEDING_BACKEND.includes(data.nodeType);

  const inlineParams = data.params.slice(0, INLINE_PARAM_LIMIT);
  const extraParams = data.params.slice(INLINE_PARAM_LIMIT);

  return (
    <div
      className={cn(
        "w-[172px] overflow-hidden rounded-lg border bg-surface shadow-sm transition-colors duration-150",
        selected ? "border-primary ring-1 ring-primary" : "border-border",
        data.disabled && "opacity-55",
      )}
    >
      <Handle type="target" position={Position.Left} style={HANDLE_STYLE} />

      <div
        className={cn("h-[5px]", status.barClass, data.status === "running" && "animate-pulse-soft")}
        aria-hidden
      />

      <div className="px-2.5 py-2">
        <div className="flex items-center gap-1.5">
          <Icon aria-hidden className="size-3.5 shrink-0 text-fg-muted" />
          <span className="min-w-0 flex-1 truncate text-xs font-semibold text-fg">{data.title}</span>
          {needsBackend && (
            <span
              title="即将支持：人工确认/条件判断/循环需要新的后端执行语义（REQ-023）"
              className="shrink-0 text-fg-subtle"
            >
              <Hourglass aria-label="即将支持" className="size-3" />
            </span>
          )}
        </div>

        <dl className="mt-1.5 space-y-0.5">
          {inlineParams.map((p) => (
            <div key={p.label} className="flex gap-1 text-[11px] leading-4">
              <dt className="shrink-0 text-fg-subtle">{p.label}</dt>
              <dd className="min-w-0 flex-1 truncate text-fg-muted">{p.value}</dd>
            </div>
          ))}
        </dl>

        {extraParams.length > 0 && (
          <>
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              aria-expanded={expanded}
              className="nodrag nopan mt-1 flex items-center gap-0.5 text-[11px] text-fg-subtle hover:text-fg-muted"
            >
              {expanded ? (
                <ChevronDown aria-hidden className="size-3" />
              ) : (
                <ChevronRight aria-hidden className="size-3" />
              )}
              更多设置 · {extraParams.length}
            </button>
            {expanded && (
              <dl className="mt-0.5 space-y-0.5 border-t border-border pt-1">
                {extraParams.map((p) => (
                  <div key={p.label} className="flex gap-1 text-[11px] leading-4">
                    <dt className="shrink-0 text-fg-subtle">{p.label}</dt>
                    <dd className="min-w-0 flex-1 truncate text-fg-muted">{p.value}</dd>
                  </div>
                ))}
              </dl>
            )}
          </>
        )}

        <div className="mt-1.5 flex items-center gap-1.5">
          <span className={cn("text-[11px] font-medium", status.textClass)}>{data.statusText}</span>
          {data.disabled && (
            <span className="rounded-sm bg-surface-2 px-1 text-[10px] text-fg-subtle">已禁用</span>
          )}
        </div>
      </div>

      <Handle type="source" position={Position.Right} style={HANDLE_STYLE} />
    </div>
  );
}
