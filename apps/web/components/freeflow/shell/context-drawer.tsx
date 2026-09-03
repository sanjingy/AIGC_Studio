// 视觉来自 ReelFlow 原型，数据由页面注入
"use client";

import * as React from "react";

import { Dialog, DialogCloseButton } from "@/components/ui/dialog";

/**
 * 右栏在 `xl` 以下的抽屉形态。
 *
 * 抽屉是**真模态**（焦点陷阱 + Esc + 遮罩），因为它盖在主内容上；宽屏那份
 * 常驻右栏则不是模态，两者只是同一批内容的两种摆法，所以内容由外面传进来
 * 一次，不在这里复制。
 *
 * 断点交给 `matchMedia` 而不是 `xl:hidden`：用 CSS 藏起来的抽屉，焦点陷阱
 * 还活着，键盘用户会被锁在一块看不见的面板里。
 */
export function ContextDrawer({
  id,
  open,
  onOpenChange,
  children,
}: {
  id: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: React.ReactNode;
}) {
  const titleId = React.useId();

  return (
    <Dialog
      id={id}
      open={open}
      onOpenChange={onOpenChange}
      labelledBy={titleId}
      placement="right"
      overlayClassName="bg-rf-overlay"
      className="h-full w-[min(20rem,calc(100vw-4.5rem))] border-l border-border bg-surface shadow-rf-card"
    >
      <div className="flex h-16 shrink-0 items-center justify-between border-b border-border px-4">
        <h2 id={titleId} className="text-sm font-semibold text-fg">
          项目上下文
        </h2>
        <DialogCloseButton
          label="关闭上下文栏"
          onClick={() => onOpenChange(false)}
          className="size-9"
        />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">{children}</div>
    </Dialog>
  );
}
