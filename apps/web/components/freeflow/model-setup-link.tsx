import Link from "next/link";
import { ArrowUpRight, Settings2 } from "lucide-react";

import { MODELS_HREF } from "@/lib/freeflow/provider-scope";
import { cn } from "@/lib/utils";

/**
 * 「去模型库」。只在 `provider.not_configured`（一个可用模型都没有）时出现：
 * 那种失败重试多少次都一样，能做的只有去配。
 *
 * `newTab`：页面上还有没提交的东西（首页的原文）时用新标签页打开，
 * 配好回来原地重试，不用先过离开拦截、也不丢草稿。
 */
export function ModelSetupLink({
  newTab = false,
  size = "md",
  className,
}: {
  newTab?: boolean;
  size?: "sm" | "md";
  className?: string;
}) {
  return (
    <Link
      href={MODELS_HREF}
      target={newTab ? "_blank" : undefined}
      rel={newTab ? "noopener" : undefined}
      title={newTab ? "在新标签页打开模型库，配好后回到这里重试" : "添加供应商并设为默认"}
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-md border border-transparent bg-primary font-medium text-primary-fg transition-colors duration-150 hover:bg-primary-hover",
        size === "sm" ? "h-7 gap-1.5 px-2.5 text-xs" : "h-9 gap-2 px-3.5 text-sm",
        className,
      )}
    >
      <Settings2 aria-hidden className={size === "sm" ? "size-3.5" : "size-4"} />
      去模型库
      {newTab && <ArrowUpRight aria-hidden className={size === "sm" ? "size-3.5" : "size-4"} />}
    </Link>
  );
}
