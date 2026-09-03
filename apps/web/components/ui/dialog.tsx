"use client";

import * as React from "react";
import { X } from "lucide-react";

import { cn } from "@/lib/utils";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * 模态容器：焦点陷阱 + Esc 关闭 + 关闭后把焦点还回去。
 *
 * **不走 portal**，面板就渲染在调用点的 DOM 位置。ReelFlow 的颜色是
 * `.theme-reelflow` 这个壳节点上的一组 CSS 变量，portal 到 `body` 之后
 * 变量继承链就断了，弹窗会变成一块没有配色的白板。代价是调用方要自己
 * 保证祖先没有 `overflow: hidden` 之外的层叠陷阱——`fixed` 元素只会被
 * 祖先的 `transform` / `filter` 困住，这两个在工作台壳里都没有。
 *
 * `dismissible=false` 用于提交中：Esc 和点遮罩都不再关闭，避免请求已经
 * 发出去、界面却先消失。
 */
export function Dialog({
  id,
  open,
  onOpenChange,
  labelledBy,
  describedBy,
  placement = "center",
  dismissible = true,
  overlayClassName,
  className,
  children,
}: {
  /** 面板元素 id，供触发按钮的 aria-controls 指过来 */
  id?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 标题元素 id；面板本身不渲染标题，由调用方决定版式 */
  labelledBy?: string;
  describedBy?: string;
  /** center = 居中弹窗，right = 从右侧贴边的抽屉 */
  placement?: "center" | "right";
  dismissible?: boolean;
  overlayClassName?: string;
  className?: string;
  children: React.ReactNode;
}) {
  const panelRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!open) return;

    const panel = panelRef.current;
    const previouslyFocused =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;

    // 打开时焦点必须进面板，否则读屏还停在触发按钮上，念的是背景内容
    const initial = panel?.querySelector<HTMLElement>(FOCUSABLE);
    (initial ?? panel)?.focus();

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        if (!dismissible) return;
        event.preventDefault();
        onOpenChange(false);
        return;
      }

      if (event.key !== "Tab" || !panel) return;

      const focusable = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      // 面板里一个可聚焦元素都没有时，Tab 会把焦点送回背景，只能钉在面板上
      if (!first || !last) {
        event.preventDefault();
        panel.focus();
        return;
      }
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      previouslyFocused?.focus();
    };
  }, [dismissible, onOpenChange, open]);

  if (!open) return null;

  return (
    <div
      className={cn(
        "fixed inset-0 z-70 flex",
        placement === "center" ? "items-center justify-center p-4" : "justify-end",
      )}
    >
      {/* 遮罩不进可访问性树：关闭有 Esc 和显式的关闭按钮两条路，
          再加一个"按钮"只会让读屏用户多读一遍 */}
      <div
        aria-hidden
        onClick={dismissible ? () => onOpenChange(false) : undefined}
        className={cn("absolute inset-0", overlayClassName ?? "bg-fg/25")}
      />
      <div
        id={id}
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        aria-describedby={describedBy}
        className={cn("relative z-10 flex flex-col outline-none", className)}
      >
        {children}
      </div>
    </div>
  );
}

/** 弹窗右上角那颗 X。单独抽出来是因为居中弹窗和右侧抽屉都要它。 */
export function DialogCloseButton({
  label = "关闭",
  disabled,
  onClick,
  className,
}: {
  label?: string;
  disabled?: boolean;
  onClick: () => void;
  className?: string;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "grid size-8 shrink-0 cursor-pointer place-items-center rounded-lg text-fg-muted",
        "transition-colors duration-150 hover:bg-surface-2 hover:text-fg",
        "disabled:pointer-events-none disabled:opacity-45",
        className,
      )}
    >
      <X aria-hidden className="size-4" />
    </button>
  );
}
