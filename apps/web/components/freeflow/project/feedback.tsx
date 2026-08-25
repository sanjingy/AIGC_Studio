"use client";

import { useCallback, useState } from "react";
import { Info, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * 原型页面共用的两个反馈件。
 *
 * 分支 B 里大量入口是「设计稿有、后端没有」的——按验收标准，这些地方
 * 必须给出明确文案，不能做成点了没反应的死按钮。所以统一走这里的
 * `useNotice()`：点一下就说清楚"为什么现在做不了"，而不是静默失败。
 *
 * 不用 `window.alert` / `confirm`：浏览器原生弹窗会阻塞整页事件，
 * 也没法走设计 token。
 */
export type NoticeTone = "info" | "danger";

export function useNotice() {
  const [notice, setNotice] = useState<{ text: string; tone: NoticeTone } | null>(null);
  const say = useCallback(
    (text: string, tone: NoticeTone = "info") => setNotice({ text, tone }),
    [],
  );
  const clear = useCallback(() => setNotice(null), []);
  return { notice, say, clear };
}

export function Notice({
  notice,
  onClose,
  className,
}: {
  notice: { text: string; tone: NoticeTone } | null;
  onClose: () => void;
  className?: string;
}) {
  if (!notice) return null;
  const danger = notice.tone === "danger";
  return (
    <div
      role="status"
      className={cn(
        "flex items-start gap-2 rounded-md border px-3 py-2 text-xs",
        danger
          ? "border-danger/25 bg-danger-soft text-danger"
          : "border-border bg-surface-2 text-fg-muted",
        className,
      )}
    >
      <Info aria-hidden className="mt-px size-3.5 shrink-0" />
      <span className="min-w-0 flex-1">{notice.text}</span>
      <button
        type="button"
        onClick={onClose}
        aria-label="关闭提示"
        className="shrink-0 cursor-pointer rounded-sm p-0.5 hover:bg-surface-3"
      >
        <X aria-hidden className="size-3" />
      </button>
    </div>
  );
}

/**
 * 二次确认弹窗。用在两个地方：
 * 「重新生成」（会再扣一次 Credits）和「删除项目」（要输入项目名）。
 *
 * `confirmDisabled` + `disabledReason` 是必须的一对：确认按钮被禁用时
 * 一定要说明为什么，否则用户只会看到一个点不动的红按钮。
 */
export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  tone = "primary",
  confirmDisabled = false,
  disabledReason,
  onCancel,
  onConfirm,
  children,
}: {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  tone?: "primary" | "danger";
  confirmDisabled?: boolean;
  disabledReason?: string;
  onCancel: () => void;
  onConfirm: () => void;
  children?: React.ReactNode;
}) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-fg/30 p-4"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="w-full max-w-[420px] rounded-lg border border-border bg-surface p-4 shadow-lg"
      >
        <h2 className="text-sm font-semibold text-fg">{title}</h2>
        <p className="mt-1.5 text-xs leading-5 text-fg-muted">{description}</p>
        {children && <div className="mt-3">{children}</div>}
        {confirmDisabled && disabledReason && (
          <p className="mt-3 rounded-md bg-surface-2 px-2.5 py-1.5 text-xs text-fg-subtle">
            {disabledReason}
          </p>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <Button size="sm" variant="ghost" onClick={onCancel}>
            取消
          </Button>
          <Button
            size="sm"
            variant={tone === "danger" ? "danger" : "primary"}
            disabled={confirmDisabled}
            title={confirmDisabled ? disabledReason : undefined}
            onClick={onConfirm}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
