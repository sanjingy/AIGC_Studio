// 视觉来自 ReelFlow 原型，数据由页面注入
import { AlertCircle } from "lucide-react";

import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";

/** 与后端 `tasks.status` 对齐（09_Database.md §8）；认不出的状态原样显示 */
const TASK_LABEL: Record<string, string> = {
  draft: "草稿",
  queued: "排队中",
  running: "生成中",
  review: "待确认",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

export interface AsideTask {
  id: string;
  title: string;
  status: string;
  progress?: number;
  error?: string;
}

/**
 * 右栏里的一条任务。
 *
 * 状态、进度、错误全部直接来自 `tasks`（ADR-008 的唯一真相），这里不加工，
 * 只做显示上的钳位——所以它和任务中心里那条永远是同一个数字。
 */
export function AsideTaskRow({ task }: { task: AsideTask }) {
  const status = task.status.toLowerCase();
  const failed = status === "failed";
  const progress =
    task.progress === undefined
      ? undefined
      : Math.round(Math.min(100, Math.max(0, task.progress)));

  return (
    <li className="rounded-xl border border-border bg-surface p-3">
      <div className="flex items-start justify-between gap-3">
        <p className="min-w-0 truncate text-xs font-medium text-fg">{task.title}</p>
        <span className={cn("shrink-0 text-[10px]", failed ? "text-danger" : "text-fg-muted")}>
          {TASK_LABEL[status] ?? task.status}
        </span>
      </div>
      {progress !== undefined && (
        <div className="mt-2.5 flex items-center gap-2">
          <Progress value={progress} label={`${task.title}进度`} className="flex-1" />
          <span className="tnum shrink-0 text-[10px] text-fg-muted">{progress}%</span>
        </div>
      )}
      {task.error && (
        <p className="mt-2 flex gap-1.5 text-[10px] leading-4 break-words text-danger">
          <AlertCircle aria-hidden className="mt-0.5 size-3 shrink-0" />
          <span className="min-w-0">{task.error}</span>
        </p>
      )}
    </li>
  );
}
