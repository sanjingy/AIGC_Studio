"use client";

import { Loader2, PanelRightOpen, Wand2 } from "lucide-react";

import { Button } from "@/components/ui/button";

import { StageRail } from "./stage-rail";
import type { StageKey, StageState } from "./workbench-shell";

type SlateField = { key: string; value: string; numeric?: boolean };

/**
 * 工作台顶栏 = 场记板 + 胶片连续带。
 *
 * 上半是场记板字段组：项目身份用「字段名 / 值」表达，而不是面包屑。
 * 没有值的字段整条不渲染，不留空槽——场记板上没写的东西就是还没定。
 *
 * 下半是阶段连续带，横贯整个内容区。它在所有断点都在：这是"我现在在哪
 * 一步"的唯一提示，窄屏更需要它，带子自己横向滚动，不撑宽页面。
 */
export function WorkbenchHeader({
  project,
  stages,
  viewingStage,
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
  primaryAction?: {
    label: string;
    onClick: () => void;
    disabled?: boolean;
    loading?: boolean;
  };
  /** 右栏在窄屏下的开关；不需要右栏时页面不传，这里就不占位 */
  asideToggle?: { controls: string; open: boolean; onOpen: () => void };
}) {
  const fields: SlateField[] = [{ key: "项目", value: project.title }];
  if (project.subtitle) fields.push({ key: "路线", value: project.subtitle });
  if (project.shots && project.shots > 0) {
    fields.push({ key: "镜头", value: String(project.shots), numeric: true });
  }
  if (project.savedAgo) {
    fields.push({ key: "更新", value: project.savedAgo, numeric: true });
  }

  return (
    <header className="ff-workbench-header z-30 shrink-0 border-b border-border bg-bg">
      <div className="flex min-h-16 flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5 lg:px-6">
        <div className="ff-slate min-w-0 flex-1">
          {fields.map((field) => (
            <div key={field.key} className="ff-slate-field">
              <span className="ff-slate-key">{field.key}</span>
              <span
                className="ff-slate-value"
                data-numeric={field.numeric ? "true" : undefined}
                title={field.value}
              >
                {field.value}
              </span>
            </div>
          ))}
        </div>

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
              size="md"
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
