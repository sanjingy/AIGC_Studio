import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * 普通页面的滚动容器。
 *
 * 外壳的 `main` 不再自带内边距和滚动条——项目页是一个占满高度、各栏
 * 独立滚动的工作区，套上统一的 `p-4 overflow-y-auto` 就会多出一根外层
 * 滚动条。所以内边距下放给页面自己拿。
 */
export function PageScroll({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cn("h-full overflow-y-auto p-4", className)}>{children}</div>
  );
}
