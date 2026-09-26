"use client";

import { ChevronRight, Loader2, PanelRightOpen, Wand2 } from "lucide-react";

import { GateApprovedIcon, GatePendingIcon, StudioMarkIcon } from "@/components/icons/studio-icons";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { StageRail } from "./stage-rail";
import type { ProductionStatus, StageKey, StageState } from "./workbench-shell";

const TONE: Record<ProductionStatus["tone"], { className: string; icon: typeof StudioMarkIcon }> = {
  neutral: { className: "border-border text-fg-muted", icon: StudioMarkIcon },
  attention: { className: "border-running/40 bg-running-soft text-running", icon: GatePendingIcon },
  done: { className: "border-success/30 bg-success-soft text-success", icon: GateApprovedIcon },
};

/**
 * 工作台顶栏：一行 48px。
 *
 * 左边是位置（项目 › 当前模块），中间是生产状态胶囊，右边是动作。
 * 位置和生产状态分开写：模块栏高亮的是"你在看哪一块"，胶囊说的是
 * "后端生产走到哪一步、门开着没有"——两者经常不同（看角色时生产可能在分镜门）。
 *
 * 胶片阶段带仍由页面决定要不要（`stages` 为空就不画）。
 */
export function WorkbenchHeader({
  project,
  stages,
  viewingStage,
  section,
  production,
  primaryAction,
  asideToggle,
}: {
  project: {
    id: string;
    title: string;
    subtitle?: string;
    savedAgo?: string;
    shots?: number;
  };
  stages: StageState[];
  viewingStage?: StageKey | null;
  section?: string;
  production?: ProductionStatus | null;
  primaryAction?: {
    label: string;
    onClick: () => void;
    disabled?: boolean;
    loading?: boolean;
  };
  /** 右栏在窄屏下的开关；不需要右栏时页面不传，这里就不占位 */
  asideToggle?: { controls: string; open: boolean; onOpen: () => void };
}) {
  const tone = production ? TONE[production.tone] : null;
  const ToneIcon = tone?.icon;

  return (
    <header className="ff-workbench-header z-30 shrink-0 border-b border-border bg-bg">
      <div className="flex h-12 items-center gap-3 px-3 sm:px-4">
        <nav aria-label="位置" className="flex min-w-0 flex-1 items-center gap-1.5 text-sm">
          <span className="min-w-0 truncate font-semibold text-fg" title={project.title}>
            {project.title}
          </span>
          {section && (
            <>
              <ChevronRight aria-hidden className="size-3.5 shrink-0 text-fg-subtle" />
              <span aria-current="page" className="shrink-0 text-fg-muted">
                {section}
              </span>
            </>
          )}
          {project.savedAgo && (
            <span className="tnum ml-2 hidden shrink-0 text-xs text-fg-subtle lg:inline">
              更新于 {project.savedAgo}
            </span>
          )}
        </nav>

        {production && tone && ToneIcon && (
          <p
            className={cn(
              "hidden shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs sm:inline-flex",
              tone.className,
            )}
          >
            <ToneIcon aria-hidden className="size-3.5 shrink-0" />
            <span className="sr-only">生产状态：</span>
            {production.label}
          </p>
        )}

        <div className="flex shrink-0 items-center gap-2">
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
              size="sm"
              aria-busy={primaryAction.loading || undefined}
              disabled={primaryAction.disabled || primaryAction.loading}
              onClick={primaryAction.onClick}
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
      </div>

      {stages.length > 0 && <StageRail stages={stages} viewing={viewingStage ?? null} />}
    </header>
  );
}
