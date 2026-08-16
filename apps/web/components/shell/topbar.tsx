"use client";

import { useTheme } from "next-themes";
import { useEffect, useState } from "react";
import { Check, Moon, Sun } from "lucide-react";

import { cn } from "@/lib/utils";
import { formatCredits } from "@/lib/utils";

/** 生产阶段。用户在 60 个镜头里穿梭时必须一眼知道自己在哪。 */
const STAGES = ["故事", "角色", "分镜", "配音", "画面", "成片"] as const;

export function Topbar({
  project,
  stage,
  credits,
}: {
  project: string;
  stage: number;
  credits: number;
}) {
  return (
    <header className="flex h-12 shrink-0 items-center gap-4 border-b border-border bg-surface px-4">
      <div className="min-w-0">
        <div className="truncate text-sm font-medium">{project}</div>
      </div>

      <StageTrack current={stage} />

      <div className="ml-auto flex items-center gap-3">
        <div className="flex items-baseline gap-1.5 rounded-md bg-surface-2 px-2.5 py-1">
          <span className="text-xs text-fg-subtle">Credits</span>
          <span className="tnum text-sm font-semibold">{formatCredits(credits)}</span>
        </div>
        <ThemeToggle />
      </div>
    </header>
  );
}

function StageTrack({ current }: { current: number }) {
  return (
    <ol className="hidden items-center gap-1 md:flex" aria-label="生产阶段">
      {STAGES.map((name, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <li key={name} className="flex items-center gap-1">
            {i > 0 && <span aria-hidden className="h-px w-3 bg-border-strong" />}
            <span
              aria-current={active ? "step" : undefined}
              className={cn(
                "flex items-center gap-1 rounded-full px-2 py-0.5 text-xs whitespace-nowrap",
                active && "bg-primary-soft font-medium text-primary",
                done && "text-success",
                !active && !done && "text-fg-subtle",
              )}
            >
              {done && <Check aria-hidden className="size-3" />}
              {name}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  // 服务端渲染时拿不到主题，直接渲染会 hydration 不匹配
  useEffect(() => setMounted(true), []);

  const dark = resolvedTheme === "dark";
  return (
    <button
      type="button"
      onClick={() => setTheme(dark ? "light" : "dark")}
      aria-label={dark ? "切换到亮色" : "切换到暗色"}
      className="flex size-7 cursor-pointer items-center justify-center rounded-md text-fg-muted transition-colors duration-150 hover:bg-surface-2 hover:text-fg"
    >
      {mounted && dark ? (
        <Sun aria-hidden className="size-4" />
      ) : (
        <Moon aria-hidden className="size-4" />
      )}
    </button>
  );
}
