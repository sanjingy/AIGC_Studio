import { cn } from "@/lib/utils";
import type { ConnectionState } from "@/lib/useProjectEvents";

const SPEC: Record<ConnectionState, { label: string; dot: string; text: string }> = {
  connecting: { label: "连接中", dot: "bg-fg-subtle", text: "text-fg-subtle" },
  live: { label: "实时", dot: "bg-success", text: "text-fg-subtle" },
  reconnecting: { label: "重连中", dot: "bg-running animate-pulse-soft", text: "text-running" },
  closed: { label: "已断开", dot: "bg-fg-subtle", text: "text-fg-subtle" },
};

/**
 * 连接状态必须可见。
 * 用户盯着一个不动的进度条时，第一个问题是"是任务卡住了还是页面掉线了"——
 * 这两种情况的处理方式完全不同，不能让他猜。
 */
export function LiveIndicator({ state }: { state: ConnectionState }) {
  const spec = SPEC[state];
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-xs", spec.text)}>
      <span aria-hidden className={cn("size-1.5 rounded-full", spec.dot)} />
      {spec.label}
    </span>
  );
}
