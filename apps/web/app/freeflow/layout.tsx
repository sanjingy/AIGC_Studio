import "./studio.css";

/**
 * `/freeflow` 的根壳。第一阶段只挂真实页面；本地预设回复的悬浮助手暂不进入
 * 项目工作台，等接入真实会话、当前实体和变更权限后再开放。
 */
export default function FreeflowRootLayout({ children }: { children: React.ReactNode }) {
  return <div className="theme-reelflow ff-studio h-dvh min-w-0 overflow-hidden bg-bg">{children}</div>;
}
