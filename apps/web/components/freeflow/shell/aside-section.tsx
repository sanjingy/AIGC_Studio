// 视觉来自 ReelFlow 原型，数据由页面注入
import * as React from "react";

/** 右栏区块的外壳：图标 + 标题 + 内容。三个区块共用，保证内边距节奏一致。 */
export function AsideSection({
  title,
  icon,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="rf-panel-card overflow-hidden rounded-2xl border border-border">
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        <span className="shrink-0 text-primary">{icon}</span>
        <h2 className="truncate text-xs font-semibold tracking-wide text-fg">{title}</h2>
      </div>
      <div className="p-4">{children}</div>
    </section>
  );
}

/** 区块内的空状态。虚线框是"这里本来会有东西"，与真实内容区分得开。 */
export function AsideEmpty({ children }: { children: React.ReactNode }) {
  return (
    <p className="rf-empty-state rounded-xl border border-dashed border-border px-3 py-5 text-center text-xs text-fg-subtle">
      {children}
    </p>
  );
}
