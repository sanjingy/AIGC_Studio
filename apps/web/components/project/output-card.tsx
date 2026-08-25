"use client";

import * as React from "react";
import { ArrowRight, type LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * 对话流里的一张结果卡片。
 *
 * 每跑完一步就在对话里留下一张，代替原来那一列折叠面板：一行摘要 +
 * 「查看」。完整内容在抽屉里看——中栏是对话，不该同时兼职当产出容器。
 *
 * 摘要不能省。卡片是折起来之后唯一的信息，写成"角色档案已完成"这种
 * 等于什么都没说，得是"5 个角色"这种能判断对不对的东西。
 */
export function OutputCard({
  icon: Icon,
  title,
  meta,
  hint,
  stale,
  onOpen,
  action,
}: {
  icon: LucideIcon;
  title: string;
  /** 一行摘要，比如「5 个节点 · 3 场景」 */
  meta: string;
  /** 摘要下面的一句补充，没有就不占位 */
  hint?: string;
  /** 上游改过、这一步已过期 */
  stale?: boolean;
  onOpen: () => void;
  /** 卡片底部的额外操作，比如「同步」 */
  action?: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "overflow-hidden rounded-[14px] border bg-surface",
        stale ? "border-running/45" : "border-border",
      )}
    >
      <button
        type="button"
        onClick={onOpen}
        className="flex w-full cursor-pointer items-center gap-2.5 px-3.5 py-3 text-left transition-colors duration-150 hover:bg-surface-2"
      >
        <Icon aria-hidden className="size-3.5 shrink-0 text-primary" />
        <span className="shrink-0 text-sm font-semibold text-fg">{title}</span>
        <span className="tnum min-w-0 flex-1 truncate text-xs text-fg-subtle">{meta}</span>
        {stale && (
          <span className="shrink-0 rounded-md bg-running-soft px-1.5 py-0.5 text-xs text-running">
            已过期
          </span>
        )}
        <ArrowRight aria-hidden className="size-3.5 shrink-0 text-fg-subtle" />
      </button>

      {(hint || action) && (
        <div className="flex flex-wrap items-center gap-2 border-t border-border px-3.5 py-2">
          {hint && <span className="min-w-0 flex-1 text-xs text-fg-subtle">{hint}</span>}
          {action}
        </div>
      )}
    </div>
  );
}
