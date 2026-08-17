"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Clapperboard, LayoutGrid, ListChecks, type LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

type Item = { href: string; label: string; icon: LucideIcon };

// 只列已经接了真实接口的页面。放一堆点进去是假数据的入口，
// 比少放几个更糟——用户分不清哪些能用。
const NAV: Item[] = [
  { href: "/dashboard", label: "项目", icon: LayoutGrid },
  { href: "/tasks", label: "任务中心", icon: ListChecks },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <nav
      aria-label="主导航"
      className="flex w-[180px] shrink-0 flex-col border-r border-border bg-surface"
    >
      <Link
        href="/dashboard"
        className="flex h-12 items-center gap-2 border-b border-border px-3 hover:bg-surface-2"
      >
        <Clapperboard aria-hidden className="size-4 text-primary" />
        <span className="text-sm font-semibold tracking-tight">AIGC Studio</span>
      </Link>

      <ul className="flex flex-col gap-0.5 p-2">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = pathname.startsWith(href);
          return (
            <li key={href}>
              <Link
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm transition-colors duration-150",
                  active
                    ? "bg-primary-soft font-medium text-primary"
                    : "text-fg-muted hover:bg-surface-2 hover:text-fg",
                )}
              >
                <Icon aria-hidden className="size-4 shrink-0" />
                {label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
