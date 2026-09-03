/**
 * 项目路由的布局。
 *
 * 这里**不再画全局 rail**：侧栏、顶栏、右栏都由 `WorkbenchShell` 提供
 * （计划 §5 的组件契约），布局再画一条会变成两条侧栏并排。
 * 每个页面自己套 `ProjectWorkbench`——壳要拿阶段、任务、一致性这些数据，
 * 而 layout 拿不到路由参数以外的东西。
 */
export default function ProjectLayout({ children }: { children: React.ReactNode }) {
  return <div className="flex h-full min-w-0 overflow-hidden">{children}</div>;
}
