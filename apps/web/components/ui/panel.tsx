import * as React from "react";

import { cn } from "@/lib/utils";

/** 工作台的基础容器。圆角 6px，不用 rounded-lg 当默认。 */
export function Panel({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("rounded-md border border-border bg-surface", className)}
      {...props}
    />
  );
}

export function PanelHeader({
  title,
  meta,
  action,
}: {
  title: React.ReactNode;
  meta?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border px-3 py-2">
      <div className="flex min-w-0 items-baseline gap-2">
        <h2 className="truncate text-sm font-semibold text-fg">{title}</h2>
        {meta && <span className="tnum shrink-0 text-xs text-fg-subtle">{meta}</span>}
      </div>
      {action}
    </div>
  );
}

/** 指标块。数字是主角，所以给它最大的字号和 tabular-nums。 */
export function Metric({
  label,
  value,
  unit,
  hint,
  tone = "default",
}: {
  label: string;
  value: string | number;
  unit?: string;
  hint?: string;
  tone?: "default" | "running" | "success" | "danger";
}) {
  const toneClass = {
    default: "text-fg",
    running: "text-running",
    success: "text-success",
    danger: "text-danger",
  }[tone];

  return (
    <div className="px-3 py-2.5">
      <div className="text-xs font-medium tracking-wide text-fg-subtle uppercase">
        {label}
      </div>
      <div className="mt-1 flex items-baseline gap-1">
        <span className={cn("tnum text-xl font-semibold", toneClass)}>{value}</span>
        {unit && <span className="text-xs text-fg-subtle">{unit}</span>}
      </div>
      {hint && <div className="mt-0.5 text-xs text-fg-subtle">{hint}</div>}
    </div>
  );
}
