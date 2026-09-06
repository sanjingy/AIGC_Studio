import {
  AlertTriangle,
  Clock,
  Loader2,
  X,
  type LucideIcon,
} from "lucide-react";

import {
  GateApprovedIcon,
  GatePendingIcon,
  MissingFrameIcon,
} from "@/components/icons/studio-icons";
import { cn } from "@/lib/utils";

/** 与后端 tasks.status 对齐（09_Database.md §8） */
export type Status =
  | "draft"
  | "queued"
  | "running"
  | "review"
  | "succeeded"
  | "failed"
  | "cancelled";

type Spec = { label: string; icon: LucideIcon; className: string; spin?: boolean };

/**
 * 状态一律「图标 + 文字 + 颜色」三重编码。
 * 不靠颜色单独传达含义——色盲用户和灰度截图都要能读。
 */
const SPEC: Record<Status, Spec> = {
  draft: {
    label: "草稿",
    icon: MissingFrameIcon,
    className: "text-fg-subtle bg-surface-2 border-border",
  },
  queued: {
    label: "排队中",
    icon: Clock,
    className: "text-fg-muted bg-surface-2 border-border",
  },
  running: {
    label: "生成中",
    icon: Loader2,
    className: "text-running bg-running-soft border-running/25",
    spin: true,
  },
  review: {
    label: "待确认",
    icon: GatePendingIcon,
    className: "text-primary bg-primary-soft border-primary/25",
  },
  succeeded: {
    label: "已完成",
    icon: GateApprovedIcon,
    className: "text-success bg-success-soft border-success/25",
  },
  failed: {
    label: "失败",
    icon: X,
    className: "text-danger bg-danger-soft border-danger/25",
  },
  cancelled: {
    label: "已取消",
    icon: AlertTriangle,
    className: "text-fg-subtle bg-surface-2 border-border",
  },
};

export function StatusChip({
  status,
  className,
  compact = false,
}: {
  status: Status;
  className?: string;
  compact?: boolean;
}) {
  const spec = SPEC[status];
  const Icon = spec.icon;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        spec.className,
        className,
      )}
    >
      <Icon aria-hidden className={cn("size-3 shrink-0", spec.spin && "animate-spin")} />
      {!compact && spec.label}
      {compact && <span className="sr-only">{spec.label}</span>}
    </span>
  );
}
