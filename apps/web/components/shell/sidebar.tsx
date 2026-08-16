"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Clapperboard,
  Coins,
  Film,
  LayoutGrid,
  Library,
  ListChecks,
  Users,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";

type Item = { href: string; label: string; icon: LucideIcon };

/** M1 的 7 个页面（01_ProductSpec.md §6）。其余里程碑再加。 */
const NAV: Item[] = [
  { href: "/dashboard", label: "工作台", icon: LayoutGrid },
  { href: "/storyboard", label: "分镜", icon: Film },
  { href: "/cast", label: "角色与场景", icon: Users },
  { href: "/assets", label: "资产库", icon: Library },
  { href: "/tasks", label: "任务中心", icon: ListChecks },
  { href: "/credits", label: "Credits", icon: Coins },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <nav
      aria-label="主导航"
      className="flex w-[200px] shrink-0 flex-col border-r border-border bg-surface"
    >
      <div className="flex h-12 items-center gap-2 border-b border-border px-3">
        <Clapperboard aria-hidden className="size-4 text-primary" />
        <span className="text-sm font-semibold tracking-tight">AIGC Studio</span>
      </div>

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
