"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/** 右键菜单外壳：定位、点外面关、Esc 关。节点菜单和「添加节点」共用。 */
export function MenuShell({
  x,
  y,
  onClose,
  label,
  children,
}: {
  x: number;
  y: number;
  onClose: () => void;
  label: string;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ x, y });

  // 在画布下沿右键时菜单会掉出视口（十三项的「添加节点」有 400 多像素高），
  // 挂上去先量一次再夹回可视区。
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    setPos({
      x: Math.max(8, Math.min(x, window.innerWidth - rect.width - 8)),
      y: Math.max(8, Math.min(y, window.innerHeight - rect.height - 8)),
    });
  }, [x, y]);

  useEffect(() => {
    const onPointerDown = (e: PointerEvent) => {
      if (!ref.current?.contains(e.target as globalThis.Node)) onClose();
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [onClose]);

  return (
    <div
      ref={ref}
      role="menu"
      aria-label={label}
      style={{ left: pos.x, top: pos.y }}
      className="fixed z-50 max-h-[70vh] min-w-[168px] overflow-y-auto rounded-lg border border-border bg-surface py-1 shadow-lg"
    >
      {children}
    </div>
  );
}

export function MenuItem({
  icon: Icon,
  children,
  onSelect,
  hint,
  danger,
}: {
  icon?: LucideIcon;
  children: React.ReactNode;
  onSelect: () => void;
  hint?: string;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onSelect}
      className={cn(
        "flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs",
        danger ? "text-danger hover:bg-danger-soft" : "text-fg-muted hover:bg-surface-2 hover:text-fg",
      )}
    >
      {Icon && <Icon aria-hidden className="size-3.5 shrink-0" />}
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {hint && <span className="shrink-0 text-[10px] text-fg-subtle">{hint}</span>}
    </button>
  );
}

export function MenuLabel({ children }: { children: React.ReactNode }) {
  return <div className="px-2.5 pb-1 pt-1.5 text-[10px] font-medium text-fg-subtle">{children}</div>;
}

export function MenuSeparator() {
  return <div className="my-1 h-px bg-border" role="separator" />;
}
