// 视觉来自 ReelFlow 原型，数据由页面注入
"use client";

import * as React from "react";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";

import { StudioMarkIcon } from "@/components/icons/studio-icons";
import { cn } from "@/lib/utils";

import { SidebarNavGroup } from "./sidebar-nav-group";
import type { NavItem } from "./workbench-shell";

/**
 * 左侧常驻导航。
 *
 * 折叠是**受控**的：壳持有这个状态，因为顶栏和主内容的可用宽度跟着它变，
 * 侧栏自己藏一份的话，另外两块拿不到。
 *
 * `md` 以下强制收成图标条，用 `max-md:` 而不是 JS 判断——首屏不必等
 * 水合就已经是对的宽度，不会先撑开再弹回去。
 */
export function SidebarNav({
  navigation,
  utilityNavigation,
  activeHref,
  collapsed,
  onCollapsedChange,
}: {
  navigation: NavItem[];
  utilityNavigation: NavItem[];
  activeHref: string;
  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
}) {
  const navId = React.useId();

  return (
    <aside
      className={cn(
        "flex h-full shrink-0 flex-col border-r border-border bg-surface",
        "transition-[width] duration-200 motion-reduce:transition-none",
        collapsed ? "w-[4.5rem]" : "w-60",
        "max-md:w-[4.5rem]",
      )}
    >
      <div className="flex h-16 items-center gap-3 border-b border-border px-4">
        <div className="grid size-9 shrink-0 place-items-center rounded-lg border border-primary/35 bg-primary-soft text-primary shadow-rf-glow">
          <StudioMarkIcon aria-hidden className="size-5" />
        </div>
        <div className={cn("min-w-0", collapsed && "hidden", "max-md:hidden")}>
          <p className="truncate text-sm font-semibold tracking-tight text-fg">AIGC Studio</p>
          <p className="truncate text-[10px] tracking-[0.14em] text-fg-subtle uppercase">
            Production workspace
          </p>
        </div>
      </div>

      <nav id={navId} aria-label="项目导航" className="min-h-0 flex-1 overflow-y-auto py-1">
        <SidebarNavGroup
          label="制作流程"
          items={navigation}
          activeHref={activeHref}
          collapsed={collapsed}
        />
        {utilityNavigation.length > 0 && (
          <div className="border-t border-border">
            <SidebarNavGroup
              label="资产与运行"
              items={utilityNavigation}
              activeHref={activeHref}
              collapsed={collapsed}
            />
          </div>
        )}
      </nav>

      <div className="border-t border-border p-2 max-md:hidden">
        <button
          type="button"
          aria-controls={navId}
          aria-expanded={!collapsed}
          aria-label={collapsed ? "展开侧栏" : "折叠侧栏"}
          onClick={() => onCollapsedChange(!collapsed)}
          className={cn(
            "flex h-10 w-full cursor-pointer items-center gap-3 rounded-xl px-3 text-sm text-fg-muted",
            "transition-colors duration-200 hover:bg-surface-2 hover:text-fg motion-reduce:transition-none",
            collapsed && "justify-center px-0",
          )}
        >
          {collapsed ? (
            <PanelLeftOpen aria-hidden className="size-4" />
          ) : (
            <PanelLeftClose aria-hidden className="size-4" />
          )}
          {!collapsed && <span>折叠侧栏</span>}
        </button>
      </div>
    </aside>
  );
}
