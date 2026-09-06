"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { AssetLibraryIcon, ModelRackIcon, ProjectIcon, StudioMarkIcon } from "@/components/icons/studio-icons";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/freeflow", label: "创作首页", icon: StudioMarkIcon },
  { href: "/freeflow/projects", label: "我的项目", icon: ProjectIcon },
  { href: "/freeflow/assets", label: "资产库", icon: AssetLibraryIcon },
  { href: "/freeflow/models", label: "模型库", icon: ModelRackIcon },
];

export function GlobalNavRail() {
  const pathname = usePathname();
  return (
    <nav aria-label="全局导航" className="ff-global-nav">
      <Link href="/freeflow" className="ff-brand" aria-label="AIGC Studio 创作首页">
        <span className="ff-brand-mark"><StudioMarkIcon aria-hidden className="size-5" /></span>
        <span className="ff-brand-name">AIGC <span className="font-normal text-fg-muted">Studio</span></span>
      </Link>
      <div className="ff-nav-heading">工作空间</div>
      <ul className="ff-nav-list">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = href === "/freeflow" ? pathname === href : pathname.startsWith(href);
          return <li key={href}><Link href={href} title={label} aria-label={label} aria-current={active ? "page" : undefined} className={cn("ff-nav-link", active && "is-active")}><Icon aria-hidden className="size-[18px] shrink-0" /><span className="ff-nav-label">{label}</span></Link></li>;
        })}
      </ul>
      <div className="ff-nav-bottom">
        <div className="ff-studio-note"><span className="ff-note-line" /><p>让故事，成为画面。</p><span>你的 AI 影像创作空间</span></div>
        <div className="ff-profile"><span className="ff-avatar">A</span><div className="ff-nav-label"><p className="text-sm font-medium">个人工作空间</p><p className="mt-1 text-xs text-fg-subtle">AIGC Studio</p></div></div>
      </div>
    </nav>
  );
}

export function GlobalPlaceholder({ title }: { title: string }) {
  return <div className="flex min-h-full flex-col items-center justify-center gap-2 p-6 text-center"><h1 className="text-lg font-semibold text-fg">{title}</h1><p className="max-w-md text-sm text-fg-subtle">此功能尚未接入；工作台只显示可执行的创作流程。</p></div>;
}
