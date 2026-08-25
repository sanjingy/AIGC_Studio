"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  CreditCard,
  FolderOpen,
  Home,
  LayoutTemplate,
  Plus,
  Server,
  Settings,
  Users,
  Wrench,
  Cpu,
  FolderKanban,
  type LucideIcon,
} from "lucide-react";

import { assets, credits, ApiRequestError, type Balance, type StorageUsage } from "@/lib/api";
import { creditsToYuan, cn } from "@/lib/utils";

type NavItem = { href: string; label: string; icon: LucideIcon };

// 01 首页需求里列的 10 项。多数是占位（分支内未展开，见 README「全局层」）——
// 只有首页和资源库有真实页面，其余点进去是"即将支持"提示，不是死链接。
const NAV: NavItem[] = [
  { href: "/freeflow", label: "首页", icon: Home },
  { href: "/freeflow/projects", label: "项目", icon: FolderKanban },
  { href: "/freeflow/templates", label: "模板", icon: LayoutTemplate },
  { href: "/freeflow/skills", label: "技能", icon: Wrench },
  { href: "/freeflow/models", label: "模型", icon: Cpu },
  { href: "/freeflow/assets", label: "资源库", icon: FolderOpen },
  { href: "/freeflow/servers", label: "服务器", icon: Server },
  { href: "/freeflow/members", label: "成员与团队", icon: Users },
  { href: "/freeflow/billing", label: "账单与订阅", icon: CreditCard },
  { href: "/freeflow/settings", label: "设置", icon: Settings },
];

/**
 * 全局层左侧常驻导航，212px。REQ-002：Token 余额和存储用量接现有
 * 计费 Ledger 真实接口（`/credits/balance`、`/assets/usage`），不是占位数字——
 * 这两个接口本来就存在，没理由假装没有。
 */
export function GlobalNavRail() {
  const pathname = usePathname();
  const [balance, setBalance] = useState<Balance | null>(null);
  const [usage, setUsage] = useState<StorageUsage | null>(null);

  useEffect(() => {
    credits
      .balance()
      .then(setBalance)
      .catch(() => setBalance(null));
    assets
      .usage()
      .then(setUsage)
      .catch(() => setUsage(null));
  }, []);

  return (
    <nav
      aria-label="全局导航"
      className="flex w-[212px] shrink-0 flex-col overflow-y-auto border-r border-border bg-surface"
    >
      <div className="flex items-center gap-2 px-3.5 py-3.5">
        <div className="flex size-6 items-center justify-center rounded-md bg-primary text-xs font-bold text-primary-fg">
          A
        </div>
        <span className="text-sm font-semibold text-fg">AIGC Studio</span>
      </div>

      <div className="px-2.5 pb-2.5">
        <Link
          href="/freeflow"
          className="flex w-full items-center justify-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-primary-fg transition-colors duration-150 hover:bg-primary-hover"
        >
          <Plus aria-hidden className="size-3.5" />
          新建项目
        </Link>
      </div>

      <ul className="flex flex-col gap-0.5 px-2.5 pb-2.5">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = href === "/freeflow" ? pathname === href : pathname.startsWith(href);
          return (
            <li key={href}>
              <Link
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm transition-colors duration-150",
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

      <div className="mt-auto flex flex-col gap-2 border-t border-border px-3.5 py-3">
        <div className="flex items-center justify-between text-xs">
          <span className="text-fg-subtle">Token 余额</span>
          <span className="tnum font-medium text-fg">
            {balance ? creditsToYuan(balance.balance) : "—"}
          </span>
        </div>
        {usage && (
          <div className="flex flex-col gap-1">
            <div className="h-1.5 overflow-hidden rounded-full bg-surface-3">
              <div
                className="h-full rounded-full bg-primary"
                style={{ width: `${Math.min(usage.percent_used, 100)}%` }}
              />
            </div>
            <span className="text-xs text-fg-subtle">
              存储 {usage.percent_used}%
              {usage.quota_bytes === null ? "（不限容量）" : ""}
            </span>
          </div>
        )}
      </div>
    </nav>
  );
}

/** 分支内为占位的全局层页面（项目/模板/技能/模型/服务器/成员/账单/设置）共用这个壳。
 *  不写成假入口——点进去老实说"即将支持"，不假装有功能。 */
export function GlobalPlaceholder({ title }: { title: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
      <h1 className="text-lg font-semibold text-fg">{title}</h1>
      <p className="text-sm text-fg-subtle">这是自由工作流原型的占位页，即将支持。</p>
    </div>
  );
}
