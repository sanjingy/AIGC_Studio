// 视觉来自 ReelFlow 原型，数据由页面注入
import * as React from "react";

/**
 * 右栏的一块。
 *
 * **不是卡片。** 上一版这里是 `border + rounded + p-4` 的面板，三块内容
 * （当前阶段、运行任务、一致性档案）各套一个，于是它们看起来一样重——
 * 那是通用 SaaS 后台侧栏的标准长相，也是这一轮自我批评要修掉的那处。
 *
 * 现在只剩「标题 + 内容」，块与块之间靠一条分隔线断开（见
 * `studio.css` 的 `.ff-rail-block`）。谁更重要由内容自己决定：
 * 当前阶段用 `.ff-rail-now` 单独抬一层底，另外两块是平的清单。
 */
export function AsideSection({
  title,
  icon,
  count,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  /** 有就显示在标题右端，等宽数字。没有就不占位。 */
  count?: number;
  children: React.ReactNode;
}) {
  return (
    <section className="ff-rail-block">
      <div className="ff-rail-head">
        <span className="shrink-0">{icon}</span>
        <h2 className="truncate">{title}</h2>
        {count !== undefined && <span>{count}</span>}
      </div>
      {children}
    </section>
  );
}

/** 区块内的空状态。一句话，不再给它一个虚线框——空本身不值一个框。 */
export function AsideEmpty({ children }: { children: React.ReactNode }) {
  return <p className="ff-rail-empty">{children}</p>;
}
