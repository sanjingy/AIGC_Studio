"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { LogOut, Moon, Plus, Sun } from "lucide-react";
import { useTheme } from "next-themes";

import { auth, credits, type Balance } from "@/lib/api";
import { cn, formatCredits } from "@/lib/utils";

export function Topbar() {
  const router = useRouter();
  const [balance, setBalance] = useState<Balance | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = () => credits.balance().then(setBalance).catch(() => setBalance(null));

  useEffect(() => {
    void refresh();
    // 生成任务会改余额，轮询比让用户手动刷新友好
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
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
    <header className="flex h-12 shrink-0 items-center gap-3 border-b border-border bg-surface px-4">
      <div className="ml-auto flex items-center gap-2">
        <div className="flex items-baseline gap-1.5 rounded-md bg-surface-2 px-2.5 py-1">
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
          className="flex cursor-pointer items-center gap-1 rounded-md border border-border-strong px-2 py-1 text-xs text-fg-muted transition-colors duration-150 hover:bg-surface-2 hover:text-fg disabled:opacity-45"
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
      </div>
    </header>
  );
}

function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const dark = resolvedTheme === "dark";
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
      {mounted && dark ? (
        <Sun aria-hidden className="size-4" />
      ) : (
        <Moon aria-hidden className="size-4" />
      )}
    </button>
  );
}
