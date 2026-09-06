// 视觉来自 ReelFlow 原型，数据由页面注入
import Link from "next/link";
import * as React from "react";

import { cn } from "@/lib/utils";

import type { NavItem } from "./workbench-shell";

/** 当前路由落在这一项或它的子路由下 */
function isCurrent(activeHref: string, href: string) {
  return activeHref === href || activeHref.startsWith(`${href}/`);
}

/**
 * 一组导航项。
 *
 * 收窄（用户折叠，或 `md` 以下自动收）时文字走 `sr-only` 而不是不渲染：
 * 图标本身没有可读名字，删掉文字等于把整条导航从读屏里抹掉。
 *
 * 没接后端的入口用 `disabled` 的 `button` 而不是 `Link`：不可点的链接
 * 仍然能被键盘聚焦、被回车打开，那就是一个假入口。
 */
export function SidebarNavGroup({
  label,
  items,
  activeHref,
  collapsed,
}: {
  label: string;
  items: NavItem[];
  activeHref: string;
  collapsed: boolean;
}) {
  const headingId = React.useId();
  if (items.length === 0) return null;

  return (
    <section aria-labelledby={headingId} className="px-2 py-3">
      <h2
        id={headingId}
        className={cn(
          "px-2 pb-2 text-[10px] font-semibold tracking-[0.18em] text-fg-subtle uppercase",
          collapsed && "sr-only",
          "max-md:sr-only",
        )}
      >
        {label}
      </h2>
      <ul className="space-y-1">
        {items.map((item) => {
          const Icon = item.icon;
          const active = !item.disabled && isCurrent(activeHref, item.href);
          const accessibleLabel =
            item.badge === undefined ? item.label : `${item.label}，${item.badge}`;
          const itemClassName = cn(
            "relative flex h-10 w-full items-center gap-3 rounded-lg border px-3 text-left text-sm font-medium",
            "transition-[color,background-color,border-color] duration-200 motion-reduce:transition-none",
            active
              ? "border-primary/20 bg-primary-soft text-primary shadow-[inset_2px_0_0_var(--primary)]"
              : "border-transparent text-fg-muted hover:border-border hover:bg-surface-2 hover:text-fg",
            item.disabled ? "cursor-not-allowed opacity-45" : "cursor-pointer",
            collapsed && "justify-center px-0",
            "max-md:justify-center max-md:px-0",
          );
          const content = (
            <>
              <Icon aria-hidden className="size-4 shrink-0" />
              <span className={cn("min-w-0 flex-1 truncate", collapsed && "sr-only", "max-md:sr-only")}>
                {item.label}
              </span>
              {item.badge !== undefined && (
                <span
                  aria-hidden
                  className={cn(
                    "tnum shrink-0 rounded-md bg-surface-2 px-1.5 py-0.5 text-[10px] text-fg-muted",
                    active && "bg-primary-soft text-primary",
                    collapsed && "hidden",
                    "max-md:hidden",
                  )}
                >
                  {item.badge}
                </span>
              )}
            </>
          );

          return (
            <li key={item.id}>
              {item.disabled ? (
                <button
                  type="button"
                  disabled
                  aria-label={`${accessibleLabel}${item.disabledReason ? `，${item.disabledReason}` : ""}`}
                  title={item.disabledReason ?? item.label}
                  className={itemClassName}
                >
                  {content}
                </button>
              ) : (
                <Link
                  href={item.href}
                  aria-label={accessibleLabel}
                  aria-current={active ? "page" : undefined}
                  title={item.label}
                  className={itemClassName}
                >
                  {content}
                </Link>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
