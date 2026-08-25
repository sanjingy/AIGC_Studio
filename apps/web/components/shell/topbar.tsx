"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ChevronRight, Clapperboard, LogOut, Moon, Plus, Sun } from "lucide-react";
import { useTheme } from "next-themes";

import { useWorkspace } from "@/components/shell/workspace";
import { auth, credits, type Balance, type User } from "@/lib/api";
import { cn, formatCredits } from "@/lib/utils";

export function Topbar() {
  const router = useRouter();
  const workspace = useWorkspace();
  const [balance, setBalance] = useState<Balance | null>(null);
  const [me, setMe] = useState<User | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = () => credits.balance().then(setBalance).catch(() => setBalance(null));

  useEffect(() => {
    void refresh();
    // 生成任务会改余额，轮询比让用户手动刷新友好
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    auth.me().then(setMe).catch(() => setMe(null));
  }, []);

  async function topup() {
    setBusy(true);
    try {
      setBalance(await credits.topup(50_000));
    } finally {
      setBusy(false);
    }
  }

  return (
    <header className="flex h-13 shrink-0 items-center gap-4 border-b border-border bg-surface px-5">
      <Link
        href="/dashboard"
        className="flex w-[188px] shrink-0 items-center gap-2 rounded-md py-1 transition-colors duration-150 hover:text-primary"
      >
        <Clapperboard aria-hidden className="size-4 text-primary" />
        <span className="text-base font-semibold tracking-tight">AIGC Studio</span>
      </Link>

      {/* 面包屑只在项目页有内容——由项目页登记到 workspace context */}
      {workspace && (
        <nav aria-label="位置" className="flex min-w-0 items-center gap-2 text-sm">
          <span className="truncate text-fg-muted">{workspace.title}</span>
          {workspace.episodeLabel && (
            <>
              <ChevronRight aria-hidden className="size-3.5 shrink-0 text-fg-subtle" />
              <span className="truncate font-medium text-fg">{workspace.episodeLabel}</span>
            </>
          )}
        </nav>
      )}

      <div className="ml-auto flex shrink-0 items-center gap-2.5">
        <div className="flex items-baseline gap-1.5 rounded-lg bg-surface-2 px-2.5 py-1">
          <span className="text-xs text-fg-subtle">Credits</span>
          <span className="tnum text-sm font-semibold">
            {balance ? formatCredits(balance.balance) : "—"}
          </span>
          {balance && balance.reserved > 0 && (
            <span className="tnum text-xs text-running">
              +{formatCredits(balance.reserved)} 预扣
            </span>
          )}
        </div>

        <button
          type="button"
          onClick={topup}
          disabled={busy}
          title="充值 ¥500（S5 只记账，未接支付通道）"
          className="flex h-7.5 cursor-pointer items-center gap-1 rounded-lg border border-border-strong px-2.5 text-xs font-medium text-fg-muted transition-colors duration-150 hover:bg-surface-2 hover:text-fg disabled:opacity-45"
        >
          <Plus aria-hidden className="size-3" />
          充值
        </button>

        <ThemeToggle />

        <button
          type="button"
          onClick={async () => {
            await auth.logout().catch(() => {});
            router.push("/login");
          }}
          aria-label="退出登录"
          className="flex size-7 cursor-pointer items-center justify-center rounded-md text-fg-muted transition-colors duration-150 hover:bg-surface-2 hover:text-fg"
        >
          <LogOut aria-hidden className="size-4" />
        </button>

        {/* 头像只是身份标识，不承载菜单——没有账号设置页可去 */}
        <div
          title={me?.email ?? undefined}
          className="flex size-7 items-center justify-center rounded-full bg-surface-3 text-xs font-semibold text-fg-muted"
        >
          {(me?.display_name || me?.email || "?").trim().charAt(0).toUpperCase()}
        </div>
      </div>
    </header>
  );
}

function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  // 挂载前一律当亮色算。`resolvedTheme` 只有客户端知道，服务端渲染不出来，
  // 直接拿它算 aria-label 会在 localStorage 存了 dark 时触发 hydration 不匹配
  // （React 会报 "attributes ... didn't match"）。图标本来就等 mounted，
  // 标签也必须一起等，否则两者还会互相矛盾。
  const dark = mounted && resolvedTheme === "dark";
  return (
    <button
      type="button"
      onClick={() => setTheme(dark ? "light" : "dark")}
      aria-label={dark ? "切换到亮色" : "切换到暗色"}
      className={cn(
        "flex size-7 cursor-pointer items-center justify-center rounded-md",
        "text-fg-muted transition-colors duration-150 hover:bg-surface-2 hover:text-fg",
      )}
    >
      {dark ? (
        <Sun aria-hidden className="size-4" />
      ) : (
        <Moon aria-hidden className="size-4" />
      )}
    </button>
  );
}
