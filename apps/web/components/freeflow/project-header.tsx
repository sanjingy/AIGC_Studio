"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ArrowLeft, Pencil, Play, Save, Share2, MoreHorizontal } from "lucide-react";

import { cn } from "@/lib/utils";

const TABS = [
  { seg: "canvas", label: "工作流" },
  { seg: "screenplay", label: "脚本" },
  { seg: "characters", label: "角色" },
  { seg: "scenes", label: "场景" },
  { seg: "storyboard", label: "分镜" },
  { seg: "assets", label: "素材" },
  { seg: "tasks", label: "任务" },
  { seg: "settings", label: "设置" },
] as const;

/**
 * 项目层 header，两行（02 工作流画布需求原文）：
 * 第一行返回箭头+项目名+编辑+工具图标+Token+头像；
 * 第二行是标签页——**真实路由**（REQ-001），不是 tab 状态切换，
 * 每个标签对应 `/freeflow/projects/[id]/<tab>` 下的一个真实页面，
 * 这样能分享链接、能刷新，也符合"不必重新设计沿用现有组件"的要求：
 * 脚本/角色/场景几个 tab 背后就是现有的 ScreenplayView 等组件。
 *
 * 没有常驻侧栏——这是分支 B 和主线四栏工作台最大的结构差异。
 */
export function ProjectHeader({
  projectId,
  title,
  onSave,
  onRun,
}: {
  projectId: string;
  title: string;
  /** 只有工作流画布页会传，其余 tab 没有"保存/运行"的概念 */
  onSave?: () => void;
  onRun?: () => void;
}) {
  const pathname = usePathname();
  const base = `/freeflow/projects/${projectId}`;

  return (
    <header className="flex shrink-0 flex-col border-b border-border bg-surface">
      <div className="flex h-14 items-center gap-2 px-4">
        <Link
          href="/freeflow"
          aria-label="返回首页"
          className="rounded-lg p-1.5 text-fg-muted hover:bg-surface-2 hover:text-fg"
        >
          <ArrowLeft aria-hidden className="size-4" />
        </Link>
        <span className="text-sm font-semibold text-fg">{title}</span>
        <button
          type="button"
          disabled
          title="原型阶段未接改名"
          className="rounded-lg p-1.5 text-fg-subtle disabled:cursor-not-allowed"
        >
          <Pencil aria-hidden className="size-3.5" />
        </button>

        <div className="ml-auto flex items-center gap-1">
          <button
            type="button"
            disabled
            title="原型阶段未接分享"
            className="rounded-lg p-1.5 text-fg-subtle disabled:cursor-not-allowed"
          >
            <Share2 aria-hidden className="size-3.5" />
          </button>
          <button
            type="button"
            disabled
            title="更多"
            className="rounded-lg p-1.5 text-fg-subtle disabled:cursor-not-allowed"
          >
            <MoreHorizontal aria-hidden className="size-3.5" />
          </button>
          <div className="mx-1.5 h-4 w-px bg-border" />
          <div className="size-7 rounded-full bg-surface-3" aria-hidden />
        </div>
      </div>

      <div className="flex h-11 items-center gap-1 px-4">
        {TABS.map((t) => {
          const href = `${base}/${t.seg}`;
          const active = pathname.startsWith(href);
          return (
            <Link
              key={t.seg}
              href={href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "rounded-lg px-2.5 py-1 text-sm transition-colors duration-150",
                active
                  ? "bg-primary-soft font-medium text-primary"
                  : "text-fg-muted hover:bg-surface-2 hover:text-fg",
              )}
            >
              {t.label}
            </Link>
          );
        })}

        {(onSave || onRun) && (
          <div className="ml-auto flex items-center gap-2">
            {onSave && (
              <button
                type="button"
                onClick={onSave}
                className="flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-sm text-fg-muted hover:bg-surface-2 hover:text-fg"
              >
                <Save aria-hidden className="size-3.5" />
                保存
              </button>
            )}
            {onRun && (
              <button
                type="button"
                onClick={onRun}
                className="flex items-center gap-1.5 rounded-lg bg-primary px-2.5 py-1 text-sm font-medium text-primary-fg hover:bg-primary-hover"
              >
                <Play aria-hidden className="size-3.5" />
                运行
              </button>
            )}
          </div>
        )}
      </div>
    </header>
  );
}
