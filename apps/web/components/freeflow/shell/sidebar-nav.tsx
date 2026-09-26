// 视觉来自 Reelbench 参考（计划 2026-09-25），数据由页面注入
"use client";

import Link from "next/link";
import * as React from "react";

import { StudioMarkIcon } from "@/components/icons/studio-icons";
import { cn } from "@/lib/utils";

import type { NavItem } from "./workbench-shell";

/** 当前路由落在这一项或它的子路由下 */
function isCurrent(activeHref: string, href: string) {
  return activeHref === href || activeHref.startsWith(`${href}/`);
}

/**
 * 模块栏：80px，图标 + 两字标签，一眼能扫完全部模块。
 *
 * 它只表达**正在查看哪一块**（`aria-current="page"`），不表达生产走到哪
 * ——生产状态在顶栏的状态胶囊里单独说。两件事混在一条导航上，
 * 用户会以为点一下「分镜」就把生产推进到了分镜。
 *
 * 窄屏（< md）收到 56px 只留图标，文字走 `sr-only`：图标本身没有可读名字，
 * 删掉文字等于把整条导航从读屏里抹掉。
 *
 * 没接后端的入口用 `disabled` 的 `button` 而不是 `Link`：不可点的链接
 * 仍然能被键盘聚焦、被回车打开，那就是一个假入口。
 */
export function SidebarNav({
  navigation,
  utilityNavigation,
  activeHref,
}: {
  navigation: NavItem[];
  utilityNavigation: NavItem[];
  activeHref: string;
}) {
  return (
    <aside className="ff-module-rail flex h-full shrink-0 flex-col border-r border-border bg-surface">
      {/* 品牌区就是回主页的入口：模块栏里全是项目内的页面，左上角回首页是通行做法 */}
      <Link
        href="/freeflow"
        aria-label="返回项目主页"
        title="返回项目主页"
        className="grid h-12 shrink-0 place-items-center border-b border-border transition-colors duration-150 hover:bg-surface-2 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary"
      >
        <span className="grid size-8 place-items-center rounded-md bg-primary text-primary-fg">
          <StudioMarkIcon aria-hidden className="size-[18px]" />
        </span>
      </Link>

      <nav aria-label="项目模块" className="min-h-0 flex-1 overflow-y-auto py-2">
        <RailGroup items={navigation} activeHref={activeHref} />
        {utilityNavigation.length > 0 && (
          <>
            <div aria-hidden className="mx-4 my-2 border-t border-border max-md:mx-3" />
            <RailGroup items={utilityNavigation} activeHref={activeHref} />
          </>
        )}
      </nav>
    </aside>
  );
}

function RailGroup({ items, activeHref }: { items: NavItem[]; activeHref: string }) {
  if (items.length === 0) return null;
  return (
    <ul className="flex flex-col gap-1 px-2 max-md:px-1.5">
      {items.map((item) => {
        const Icon = item.icon;
        const active = !item.disabled && isCurrent(activeHref, item.href);
        const accessibleLabel =
          item.badge === undefined ? item.label : `${item.label}，${item.badge}`;
        const className = cn(
          "ff-module-link relative flex w-full flex-col items-center justify-center gap-1 rounded-md py-2 text-[12px] leading-none",
          "transition-colors duration-150 motion-reduce:transition-none",
          "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary",
          active ? "bg-primary-soft text-primary" : "text-fg-muted hover:bg-surface-2 hover:text-fg",
          item.disabled ? "cursor-not-allowed opacity-45" : "cursor-pointer",
        );
        const content = (
          <>
            <Icon aria-hidden className="size-[18px] shrink-0" />
            <span className="max-md:sr-only">{item.shortLabel ?? item.label}</span>
            {item.badge !== undefined && (
              <span
                aria-hidden
                className="tnum absolute top-1 right-1.5 min-w-4 rounded-full bg-running px-1 text-center text-[10px] leading-4 text-primary-fg"
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
                className={className}
              >
                {content}
              </button>
            ) : (
              <Link
                href={item.href}
                aria-label={accessibleLabel}
                aria-current={active ? "page" : undefined}
                title={item.label}
                className={className}
              >
                {content}
              </Link>
            )}
          </li>
        );
      })}
    </ul>
  );
}
