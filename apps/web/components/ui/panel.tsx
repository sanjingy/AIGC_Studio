import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * 工作台的基础容器。走画框半径（2px）——结构是方的，圆角留给控件。
 *
 * 只剩 `Disclosure` 在用。数据页（模型库、任务中心、项目设置）已经改走
 * `.ff-ledger` 的账式行，原来配套的 `PanelHeader` / `Metric` 随之删掉——
 * 留着会让下一个人以为「数据页应该用 Panel」，那正是这轮要改掉的版式。
 */
export function Panel({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("rounded-[2px] border border-border bg-surface", className)}
      {...props}
    />
  );
}
