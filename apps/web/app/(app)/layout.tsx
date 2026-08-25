import { Sidebar } from "@/components/shell/sidebar";
import { Topbar } from "@/components/shell/topbar";
import { WorkspaceProvider } from "@/components/shell/workspace";

/**
 * 工作台外壳：顶栏横贯整屏，下面才是左栏 + 内容。
 *
 * `main` 不带内边距、不自己滚——项目页是一个占满高度的三栏工作区，
 * 各栏各自滚；带内边距的普通页面自己套 `<PageScroll>`。
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <WorkspaceProvider>
      <div className="flex h-dvh flex-col overflow-hidden">
        <Topbar />
        <div className="flex min-h-0 flex-1">
          <Sidebar />
          <main className="min-h-0 min-w-0 flex-1 overflow-hidden">{children}</main>
        </div>
      </div>
    </WorkspaceProvider>
  );
}
