// 视觉来自 ReelFlow 原型，数据由页面注入
import {
  Check,
  ChevronRight,
  Circle,
  Clock3,
  LockKeyhole,
  Radio,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";

import type { StageState } from "./workbench-shell";

type StateMeta = { label: string; icon: LucideIcon; shell: string; number: string; text: string };

/** 每个状态都带图标和文字，颜色只是第三重编码，去掉颜色也读得懂 */
const STATE_META: Record<StageState["state"], StateMeta> = {
  locked: {
    label: "已锁定",
    icon: LockKeyhole,
    shell: "bg-transparent",
    number: "text-fg-subtle",
    text: "text-fg-muted",
  },
  ready: {
    label: "已就绪",
    icon: Circle,
    shell: "bg-surface-2",
    number: "text-primary",
    text: "text-fg",
  },
  approved: {
    label: "已审核",
    icon: Check,
    shell: "bg-success-soft",
    number: "text-success",
    text: "text-fg",
  },
  active: {
    label: "当前阶段",
    icon: Radio,
    shell: "bg-primary-soft shadow-rf-glow",
    number: "text-primary",
    text: "text-fg",
  },
  pending: {
    label: "待开始",
    icon: Clock3,
    shell: "bg-transparent",
    number: "text-fg-subtle",
    text: "text-fg-subtle",
  },
};

/**
 * 阶段进度条。**只读**——阶段图在后端 `orchestrator._NEXT` 里，
 * 前端重排不了，做成可拖拽就是个假入口。
 */
export function StageRail({ stages }: { stages: StageState[] }) {
  const items = stages ?? [];
  if (items.length === 0) return null;

  return (
    <nav aria-label="制作阶段" className="min-w-0 overflow-x-auto">
      <ol className="flex min-w-max items-center gap-0.5 rounded-xl border border-border bg-surface-2 p-1">
        {items.map((stage, index) => {
          const meta = STATE_META[stage.state];
          const Icon = meta.icon;

          return (
            <li
              key={stage.key}
              aria-current={stage.state === "active" ? "step" : undefined}
              className="flex items-center"
            >
              <div
                className={cn(
                  "min-w-[6.25rem] rounded-lg px-2.5 py-1.5",
                  "transition-colors duration-200 motion-reduce:transition-none",
                  meta.shell,
                )}
              >
                <div className="flex items-center gap-1.5">
                  <span className={cn("tnum font-mono text-[10px]", meta.number)}>
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <span className={cn("text-xs font-semibold", meta.text)}>{stage.label}</span>
                </div>
                <span className="mt-0.5 flex items-center gap-1 pl-5 text-[10px] text-fg-subtle">
                  <Icon aria-hidden className="size-2.5 shrink-0" />
                  {meta.label}
                </span>
              </div>
              {index < items.length - 1 && (
                <ChevronRight aria-hidden className="mx-0.5 size-3 shrink-0 text-fg-subtle" />
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
