import { FloatingAssistant } from "@/components/freeflow/floating-assistant";

/**
 * "分支 B · 自由工作流" 原型的根壳，挂在独立路径 `/freeflow` 下，
 * 不碰 `app/(app)/...` 主线四栏工作台（REQ-000 尚未决定要不要替换，
 * 这轮只做前端可点击原型验证方向，见对话记录）。
 *
 * 只做一件事：挂悬浮 AI 助手。它要跨越"全局层"和"项目层"两套子布局
 * 保留展开/收起状态和对话历史（README「悬浮 AI 助手」要求），所以必须
 * 挂在两级布局之上的这一层，不能放进 (global)/layout.tsx 或
 * projects/[id]/layout.tsx 任何一个，否则切换层级时会被卸载重建。
 */
export default function FreeflowRootLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="h-dvh bg-bg">
      {children}
      <FloatingAssistant />
    </div>
  );
}
