"use client";

import { useState } from "react";
import { BaseEdge, EdgeLabelRenderer, getBezierPath, type EdgeProps } from "@xyflow/react";
import { X } from "lucide-react";

import { cn } from "@/lib/utils";

import { useCanvasActions } from "./canvas-actions";

/**
 * 连线：选中或悬停时在中点浮出一个 ×（需求 3.1「删除连线：点选后 Delete，
 * 或悬停显示 ×」）。可见线只有 1.5px，鼠标压不准，所以额外画一条透明的
 * 20px 粗路径专门吃指针事件。
 */
export function DeletableEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
  selected,
}: EdgeProps) {
  const [hovered, setHovered] = useState(false);
  const { deleteEdge } = useCanvasActions();

  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  const active = selected || hovered;

  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        // 关掉 BaseEdge 自带的那条命中路径，换成下面这条——一模一样的粗细，
        // 但挂得上悬停回调。两条一起画会让 DOM 里出现两条重复的命中路径。
        interactionWidth={0}
        style={{
          stroke: active ? "var(--primary)" : "var(--border-strong)",
          strokeWidth: active ? 2 : 1.5,
        }}
      />
      <path
        d={path}
        fill="none"
        stroke="transparent"
        strokeWidth={20}
        className="react-flow__edge-interaction"
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      />
      <EdgeLabelRenderer>
        <button
          type="button"
          aria-label="删除连线"
          onMouseEnter={() => setHovered(true)}
          onMouseLeave={() => setHovered(false)}
          onClick={(e) => {
            e.stopPropagation();
            deleteEdge(id);
          }}
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          className={cn(
            "nodrag nopan pointer-events-auto absolute flex size-4 items-center justify-center rounded-full border border-border bg-surface text-fg-muted shadow-sm transition-opacity duration-150 hover:border-danger hover:text-danger",
            active ? "opacity-100" : "pointer-events-none opacity-0",
          )}
        >
          <X aria-hidden className="size-2.5" />
        </button>
      </EdgeLabelRenderer>
    </>
  );
}
