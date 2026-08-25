/**
 * 项目层壳。故意什么都不做，只做路由分组——`<ProjectHeader>` 需要项目
 * 标题（要请求）和「保存/运行」按钮（只有工作流画布有），这些每个 tab
 * 不一样，放不进一层共享布局，所以由每个 tab 页自己渲染 header。
 */
export default function ProjectLayout({ children }: { children: React.ReactNode }) {
  return <div className="flex h-full flex-col">{children}</div>;
}
