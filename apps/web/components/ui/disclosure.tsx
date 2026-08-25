"use client";

import * as React from "react";
import { ChevronRight } from "lucide-react";

import { Panel } from "@/components/ui/panel";
import { cn } from "@/lib/utils";

/**
 * 折叠面板。视觉上就是一个 Panel + 可点的 PanelHeader，
 * 圆角、边框、内边距节奏与 Panel/PanelHeader 完全一致——
 * 折叠只是行为，不是另一套设计语言。
 *
 * **受控组件**：展开状态由上层持有。页面要能从别处（比如阶段进度条）
 * 把某一组展开并滚过去，自己藏一份 open 状态就做不到。
 *
 * 标题区是按钮，右侧的 action 放在按钮**外面**——按钮套按钮是非法 HTML，
 * 浏览器会把内层按钮的点击一起交给外层，"同步"按钮会顺手把面板折起来。
 */
export function Disclosure({
  id,
  title,
  meta,
  open,
  onToggle,
  action,
  className,
  children,
}: {
  /** 供进度条 scrollIntoView 定位用 */
  id?: string;
  title: React.ReactNode;
  meta?: React.ReactNode;
  open: boolean;
  onToggle: () => void;
  action?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}) {
  const bodyId = React.useId();

  return (
    <Panel id={id} className={cn("scroll-mt-4", className)}>
      <div
        className={cn(
          "flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 px-3 py-2",
          open && "border-b border-border",
        )}
      >
        <button
          type="button"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={onToggle}
          className="flex min-w-0 flex-1 cursor-pointer items-baseline gap-2 text-left transition-colors duration-150"
        >
          <ChevronRight
            aria-hidden
            className={cn(
              "size-3.5 shrink-0 self-center text-fg-subtle transition-transform duration-150",
              open && "rotate-90",
            )}
          />
          <span className="truncate text-sm font-semibold text-fg">{title}</span>
          {meta && <span className="tnum shrink-0 text-xs text-fg-subtle">{meta}</span>}
        </button>
        {action}
      </div>
      {/* 用 class 隐藏而不是卸载：折起来再展开时，表格的横向滚动位置
          和已经加载的内容都还在，展开不会闪一下 */}
      <div id={bodyId} className={cn(!open && "hidden")}>
        {children}
      </div>
    </Panel>
  );
}
