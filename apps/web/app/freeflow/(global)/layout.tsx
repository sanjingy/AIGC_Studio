import { GlobalNavRail } from "@/components/freeflow/global-nav-rail";
import { GlobalTopbar } from "@/components/freeflow/global-topbar";

/** 全局层：左侧常驻导航 + 顶栏，见需求文档「全局导航与路由」表。 */
export default function GlobalLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full">
      <GlobalNavRail />
      <div className="flex min-w-0 flex-1 flex-col">
        <GlobalTopbar />
        <main className="min-h-0 min-w-0 flex-1 overflow-y-auto">{children}</main>
      </div>
    </div>
  );
}
