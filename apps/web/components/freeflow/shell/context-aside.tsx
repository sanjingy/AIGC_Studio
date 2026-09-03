// 视觉来自 ReelFlow 原型，数据由页面注入
"use client";

import { AlertCircle, Check, ChevronRight, CircleDashed, Loader2, ShieldCheck } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { AsideEmpty, AsideSection } from "./aside-section";
import { AsideTaskRow } from "./aside-task-row";
import type { StageState } from "./workbench-shell";

const STAGE_LABEL: Record<StageState["state"], string> = {
  locked: "已锁定",
  ready: "已就绪",
  approved: "已审核",
  active: "当前阶段",
  pending: "待开始",
};

const GATE_COPY = {
  needs_review: { label: "待确认", tone: "text-rf-warning bg-rf-warning-soft" },
  approved: { label: "已通过", tone: "text-success bg-success-soft" },
  rejected: { label: "已退回", tone: "text-danger bg-danger-soft" },
  none: { label: "无确认门", tone: "text-fg-subtle bg-surface-2" },
} as const;

/**
 * 当前阶段卡。确认门的两颗按钮只在 `needs_review` 且页面真的给了回调时出现——
 * 门是后端的状态，页面没接线就不该画出一颗点了没反应的「通过」。
 */
export function AsideStageCard(props: {
  stage: StageState;
  gate?: {
    status: "needs_review" | "approved" | "rejected" | "none";
    onApprove?: () => void;
    onReject?: () => void;
    busy?: boolean;
  };
}) {
  const { stage, gate } = props;
  const gateCopy = gate ? GATE_COPY[gate.status] : undefined;
  const showGateActions =
    gate?.status === "needs_review" && Boolean(gate.onApprove || gate.onReject);

  return (
    <AsideSection title="当前阶段" icon={<CircleDashed aria-hidden className="size-4" />}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-fg">{stage.label}</p>
          <p className="mt-1 text-xs text-fg-muted">{STAGE_LABEL[stage.state]}</p>
        </div>
        {gateCopy && (
          <span
            className={cn(
              "shrink-0 rounded-full px-2 py-1 text-[10px] font-semibold",
              gateCopy.tone,
            )}
          >
            {gateCopy.label}
          </span>
        )}
      </div>

      {showGateActions && (
        <div className="mt-4 grid grid-cols-2 gap-2">
          {gate?.onReject && (
            <Button
              variant="ghost"
              size="sm"
              disabled={gate.busy}
              onClick={gate.onReject}
              className="border border-border"
            >
              退回
            </Button>
          )}
          {gate?.onApprove && (
            <Button
              variant="primary"
              size="sm"
              aria-busy={gate.busy || undefined}
              disabled={gate.busy}
              onClick={gate.onApprove}
              className={cn(!gate.onReject && "col-span-2")}
            >
              {gate.busy && <Loader2 aria-hidden className="size-3.5 animate-spin" />}
              通过
            </Button>
          )}
        </div>
      )}
    </AsideSection>
  );
}

export function AsideTaskList(props: {
  tasks: Array<{
    id: string;
    title: string;
    status: string;
    progress?: number;
    error?: string;
  }>;
  onViewAll: () => void;
}) {
  const { onViewAll } = props;
  const tasks = props.tasks ?? [];

  return (
    <AsideSection title="运行任务" icon={<Loader2 aria-hidden className="size-4" />}>
      {tasks.length === 0 ? (
        <AsideEmpty>暂无运行任务</AsideEmpty>
      ) : (
        <ul className="space-y-2">
          {tasks.map((task) => (
            <AsideTaskRow key={task.id} task={task} />
          ))}
        </ul>
      )}
      <button
        type="button"
        onClick={onViewAll}
        className="mt-3 flex w-full cursor-pointer items-center justify-between rounded-lg px-2 py-2 text-xs font-medium text-fg-muted transition-colors duration-200 hover:bg-surface-2 hover:text-fg motion-reduce:transition-none"
      >
        查看全部任务
        <ChevronRight aria-hidden className="size-3.5 shrink-0" />
      </button>
    </AsideSection>
  );
}

/**
 * 一致性档案的就绪情况。`ok` 决定图标和颜色，图标另外带一句 sr-only 文字——
 * 一列绿勾红叹号在灰度截图里是分不出来的。
 */
export function AsideConsistency(props: {
  items: Array<{ label: string; value: string; ok: boolean }>;
}) {
  const items = props.items ?? [];

  return (
    <AsideSection title="一致性档案" icon={<ShieldCheck aria-hidden className="size-4" />}>
      {items.length === 0 ? (
        <AsideEmpty>还没有一致性档案</AsideEmpty>
      ) : (
        <ul className="space-y-2">
          {items.map((item) => (
            <li
              key={item.label}
              className="flex items-center gap-3 rounded-xl border border-border bg-surface p-3"
            >
              <span
                className={cn(
                  "grid size-7 shrink-0 place-items-center rounded-lg",
                  item.ok ? "bg-success-soft text-success" : "bg-rf-warning-soft text-rf-warning",
                )}
              >
                {item.ok ? (
                  <Check aria-hidden className="size-3.5" />
                ) : (
                  <AlertCircle aria-hidden className="size-3.5" />
                )}
                <span className="sr-only">{item.ok ? "已就绪" : "需处理"}</span>
              </span>
              <div className="min-w-0">
                <p className="truncate text-xs text-fg-muted">{item.label}</p>
                <p
                  className={cn(
                    "mt-0.5 truncate text-[10px]",
                    item.ok ? "text-success" : "text-rf-warning",
                  )}
                >
                  {item.value}
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </AsideSection>
  );
}
