"use client";

import * as React from "react";
import { X } from "lucide-react";

/**
 * 产出抽屉。
 *
 * 中栏现在只有对话，产出不再堆成一列折叠面板——点对话里的结果卡片，
 * 从右侧滑出这个抽屉看完整内容。
 *
 * 宽度给到 1100：分镜表 9 列，最小 860，塞在 680 宽的对话流里只能横向
 * 滚着看，而那张表本来就是要一眼扫的。抽屉不受三栏宽度限制，这是把它
 * 从中栏搬出来最主要的收益。
 *
 * Esc 关闭、点遮罩关闭、打开时锁住背景滚动——三件事缺一件都会让人
 * 觉得"这个面板关不掉"。
 */
export function OutputDrawer({
  open,
  title,
  meta,
  action,
  onClose,
  children,
}: {
  open: boolean;
  title: React.ReactNode;
  meta?: React.ReactNode;
  /** 标题栏右侧的操作位，比如「同步」 */
  action?: React.ReactNode;
  onClose: () => void;
  children: React.ReactNode;
}) {
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-modal="true">
      {/* 遮罩。不要做成全黑——工作台的用户需要还能看见底下自己在哪一步 */}
      <button
        type="button"
        aria-label="关闭"
        onClick={onClose}
        className="absolute inset-0 cursor-default bg-fg/25"
      />

      <aside className="relative flex h-full w-[min(1100px,92vw)] flex-col border-l border-border bg-surface shadow-2xl">
        <div className="flex h-13 shrink-0 items-center gap-3 border-b border-border px-4">
          <h2 className="truncate text-sm font-semibold text-fg">{title}</h2>
          {meta && <span className="tnum truncate text-xs text-fg-subtle">{meta}</span>}
          <div className="ml-auto flex shrink-0 items-center gap-2">
            {action}
            <button
              type="button"
              onClick={onClose}
              aria-label="关闭"
              className="flex size-7 cursor-pointer items-center justify-center rounded-md text-fg-muted transition-colors duration-150 hover:bg-surface-2 hover:text-fg"
            >
              <X aria-hidden className="size-4" />
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      </aside>
    </div>
  );
}
