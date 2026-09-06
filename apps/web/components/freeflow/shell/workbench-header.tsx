// 视觉来自 ReelFlow 原型，数据由页面注入
"use client";

import { Loader2, PanelRightOpen, Wand2 } from "lucide-react";

import { Button } from "@/components/ui/button";

import { StageRail } from "./stage-rail";
import type { StageState } from "./workbench-shell";

/**
 * 工作台顶栏：项目身份、阶段进度、主动作。
 *
 * 阶段条在 `lg` 以下用 `order-last` 掉到第二行而不是隐藏——它是"我现在
 * 在哪一步"的唯一提示，窄屏更需要它，只是不该跟标题抢第一行。
 */
export function WorkbenchHeader({
  project,
  stages,
  primaryAction,
  asideToggle,
}: {
  project: { id: string; title: string; subtitle?: string; savedAgo?: string };
  stages: StageState[];
  primaryAction?: {
    label: string;
    onClick: () => void;
    disabled?: boolean;
    loading?: boolean;
  };
  /** 右栏在窄屏下的开关；不需要右栏时页面不传，这里就不占位 */
  asideToggle?: { controls: string; open: boolean; onOpen: () => void };
}) {
  return (
    <header className="ff-workbench-header z-30 flex min-h-16 shrink-0 flex-wrap items-center gap-3 border-b border-border bg-bg px-4 py-2 lg:flex-nowrap lg:px-6">
      <div className="min-w-0 shrink-0 lg:w-44">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-sm font-semibold text-fg">{project.title}</span>
          {project.subtitle && (
            <span className="hidden max-w-28 shrink-0 truncate rounded-full border border-border px-2 py-0.5 text-[10px] text-fg-muted sm:inline">
              {project.subtitle}
            </span>
          )}
        </div>
        {project.savedAgo && (
          <p className="mt-0.5 truncate text-[10px] text-fg-subtle">{project.savedAgo}</p>
        )}
      </div>

      {stages.length > 0 && (
        <div className="order-last hidden min-w-0 flex-1 lg:order-none lg:block">
          <StageRail stages={stages} />
        </div>
      )}

      <div className="ml-auto flex shrink-0 items-center gap-2">
        {asideToggle && (
          <Button
            variant="ghost"
            size="sm"
            aria-label="打开项目上下文"
            aria-controls={asideToggle.controls}
            aria-expanded={asideToggle.open}
            onClick={asideToggle.onOpen}
            className="border border-border xl:hidden"
          >
            <PanelRightOpen aria-hidden className="size-4" />
            <span className="hidden sm:inline">上下文</span>
          </Button>
        )}
        {primaryAction && (
          <Button
            variant="primary"
            size="md"
            aria-busy={primaryAction.loading || undefined}
            disabled={primaryAction.disabled || primaryAction.loading}
            onClick={primaryAction.onClick}
            className="rounded-xl shadow-rf-glow"
          >
            {primaryAction.loading ? (
              <Loader2 aria-hidden className="size-4 animate-spin" />
            ) : (
              <Wand2 aria-hidden className="size-4" />
            )}
            <span className="truncate">{primaryAction.label}</span>
          </Button>
        )}
      </div>
    </header>
  );
}
