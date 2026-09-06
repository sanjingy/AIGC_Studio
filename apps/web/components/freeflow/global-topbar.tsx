"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChevronRight } from "lucide-react";
import { AssetLibraryIcon } from "@/components/icons/studio-icons";

const TITLES: Record<string, string> = {
  "/freeflow": "创作首页", "/freeflow/projects": "我的项目", "/freeflow/assets": "资产库",
  "/freeflow/models": "模型库", "/freeflow/settings": "设置", "/freeflow/billing": "账单",
};

export function GlobalTopbar() {
  const pathname = usePathname();
  return <header className="ff-topbar"><div className="flex min-w-0 flex-1 items-center gap-3 text-sm"><span className="hidden text-fg-subtle sm:inline">工作空间</span><ChevronRight aria-hidden className="hidden size-3.5 text-fg-subtle sm:block" /><span className="truncate font-medium text-fg">{TITLES[pathname] ?? "创作工作台"}</span></div><Link href="/freeflow/assets" className="ff-quiet-button"><AssetLibraryIcon aria-hidden className="size-4" /><span>浏览资产</span></Link></header>;
}
