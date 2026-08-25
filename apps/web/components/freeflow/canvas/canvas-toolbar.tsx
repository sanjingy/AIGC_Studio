"use client";

import { Maximize2, Minus, Plus, Redo2, Undo2 } from "lucide-react";

import { cn } from "@/lib/utils";

const BTN =
  "flex size-6 items-center justify-center rounded-md text-fg-muted hover:bg-surface-2 hover:text-fg disabled:cursor-not-allowed disabled:text-fg-subtle disabled:hover:bg-transparent";

/** 底部缩放 / 撤销重做条（设计稿 02 屏底栏）。 */
export function CanvasToolbar({
  zoom,
  onZoomIn,
  onZoomOut,
  onZoomReset,
  onFitView,
  onUndo,
  onRedo,
  canUndo,
  canRedo,
  hint,
}: {
  zoom: number;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onZoomReset: () => void;
  onFitView: () => void;
  onUndo: () => void;
  onRedo: () => void;
  canUndo: boolean;
  canRedo: boolean;
  hint: string;
}) {
  return (
    <div className="flex h-9 shrink-0 items-center gap-1.5 border-t border-border bg-surface px-3 text-xs text-fg-muted">
      <button type="button" onClick={onZoomOut} aria-label="缩小" title="缩小" className={BTN}>
        <Minus aria-hidden className="size-3.5" />
      </button>
      <button
        type="button"
        onClick={onZoomReset}
        title="恢复 100%"
        className="tnum min-w-11 rounded-md px-1 py-0.5 text-center hover:bg-surface-2 hover:text-fg"
      >
        {Math.round(zoom * 100)}%
      </button>
      <button type="button" onClick={onZoomIn} aria-label="放大" title="放大" className={BTN}>
        <Plus aria-hidden className="size-3.5" />
      </button>
      <button
        type="button"
        onClick={onFitView}
        aria-label="适应画布"
        title="适应画布"
        className={BTN}
      >
        <Maximize2 aria-hidden className="size-3.5" />
      </button>

      <div className="mx-1 h-3.5 w-px bg-border" />

      <button
        type="button"
        onClick={onUndo}
        disabled={!canUndo}
        aria-label="撤销"
        title="撤销（Ctrl+Z）"
        className={BTN}
      >
        <Undo2 aria-hidden className="size-3.5" />
      </button>
      <button
        type="button"
        onClick={onRedo}
        disabled={!canRedo}
        aria-label="重做"
        title="重做（Ctrl+Shift+Z）"
        className={cn(BTN, "mr-2")}
      >
        <Redo2 aria-hidden className="size-3.5" />
      </button>

      <span className="truncate text-[11px] text-fg-subtle">{hint}</span>
    </div>
  );
}
