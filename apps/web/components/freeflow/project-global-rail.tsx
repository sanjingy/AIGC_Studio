"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { FolderKanban, Home, Images, type LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

type RailItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  active: (pathname: string) => boolean;
};

/**
 * 项目模式只保留已经可用的全局入口。项目阶段放在 ProjectHeader，避免把
 * 全局导航和项目导航混成一条十几个入口的长列表。
 */
const ITEMS: RailItem[] = [
  {
    href: "/freeflow",
    label: "工作台",
    icon: Home,
    active: (pathname) => pathname === "/freeflow",
  },
  {
    href: "/freeflow/projects",
    label: "项目",
    icon: FolderKanban,
    active: (pathname) => pathname.startsWith("/freeflow/projects"),
  },
  {
    href: "/freeflow/assets",
    label: "资产库",
    icon: Images,
    active: (pathname) => pathname.startsWith("/freeflow/assets"),
  },
];

export function ProjectGlobalRail() {
  const pathname = usePathname();

  return (
    <nav
      aria-label="全局导航"
      className="hidden w-[72px] shrink-0 flex-col border-r border-border bg-surface md:flex 2xl:w-20"
    >
      <Link
        href="/freeflow"
        aria-label="返回 AIGC Studio 工作台"
        title="AIGC Studio"
        className="mx-auto mt-3 flex size-9 items-center justify-center rounded-lg bg-primary text-sm font-bold text-primary-fg transition-colors duration-150 hover:bg-primary-hover"
      >
        A
      </Link>

      <ul className="mt-5 flex flex-col gap-1 px-2">
        {ITEMS.map(({ href, label, icon: Icon, active }) => {
          const current = active(pathname);
          return (
            <li key={href}>
              <Link
                href={href}
                aria-label={label}
                aria-current={current ? "page" : undefined}
                title={label}
                className={cn(
                  "flex min-h-11 flex-col items-center justify-center gap-1 rounded-lg px-1 py-2 text-[10px] leading-none transition-colors duration-150",
                  current
                    ? "bg-primary-soft font-medium text-primary"
                    : "text-fg-subtle hover:bg-surface-2 hover:text-fg",
                )}
              >
                <Icon aria-hidden className="size-4 shrink-0" />
                <span className="hidden 2xl:inline">{label}</span>
                <span className="sr-only 2xl:hidden">{label}</span>
              </Link>
            </li>
          );
        })}
      </ul>

      <div className="mt-auto px-2 pb-3 text-center text-[10px] leading-4 text-fg-subtle">
        <span aria-hidden className="hidden 2xl:inline">
          项目模式
        </span>
      </div>
    </nav>
  );
}
