"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Boxes, FolderKanban, Home, Images, Plus, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

type NavItem = { href: string; label: string; icon: LucideIcon };

/**
 * 导航里只放已经接了真实后端的页面（FR-WEB-011）。
 *
 * 模型页读 `/model-catalog` 和 `/provider-credentials`，是真页面，所以进导航
 * （决策记录 §11.5 裁决 10）。成员 / 服务器 / 模板 / 技能四个占位路由已删；
 * 账单与设置两个占位页留在路由里但不进这张表——不在导航里就不是假入口
 * （同 §11.5 裁决 11）。节点画布按 ADR-030 第 5 条隐藏，代码保留。
 */
const NAV: NavItem[] = [
  { href: "/freeflow", label: "首页", icon: Home },
  { href: "/freeflow/projects", label: "项目", icon: FolderKanban },
  { href: "/freeflow/assets", label: "资产", icon: Images },
  { href: "/freeflow/models", label: "模型", icon: Boxes },
];

export function GlobalNavRail() {
  const pathname = usePathname();
  return <nav aria-label="全局导航" className="hidden w-[72px] shrink-0 flex-col border-r border-border bg-surface md:flex 2xl:w-20">
    <div className="flex h-14 items-center justify-center border-b border-border"><div className="flex size-8 items-center justify-center rounded-md bg-primary text-xs font-bold text-primary-fg">A</div></div>
    <div className="px-3 pt-3"><Link href="/freeflow" aria-label="新建项目" title="新建项目" className="flex min-h-11 items-center justify-center rounded-md bg-primary text-primary-fg transition-colors duration-150 hover:bg-primary-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"><Plus aria-hidden className="size-4" /></Link></div>
    <ul className="mt-3 flex flex-col gap-1 px-3">{NAV.map(({ href, label, icon: Icon }) => {
      const active = href === "/freeflow" ? pathname === href : pathname.startsWith(href);
      return <li key={href}><Link href={href} aria-label={label} title={label} aria-current={active ? "page" : undefined} className={cn("flex min-h-11 flex-col items-center justify-center gap-1 rounded-md px-1 py-2 text-[10px] transition-colors duration-150 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary", active ? "bg-primary-soft font-medium text-primary" : "text-fg-muted hover:bg-surface-2 hover:text-fg")}><Icon aria-hidden className="size-4" /><span className="hidden 2xl:inline">{label}</span><span className="sr-only 2xl:hidden">{label}</span></Link></li>;
    })}</ul>
  </nav>;
}

export function GlobalPlaceholder({ title }: { title: string }) { return <div className="flex min-h-full flex-col items-center justify-center gap-2 p-6 text-center"><h1 className="text-lg font-semibold text-fg">{title}</h1><p className="max-w-md text-sm text-fg-subtle">此功能尚未接入；工作台只显示可执行的创作流程。</p></div>; }
