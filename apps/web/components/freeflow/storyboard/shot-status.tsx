// 视觉来自 ReelFlow 原型，数据由页面注入
import { Loader2, X, type LucideIcon } from "lucide-react";

import { GateApprovedIcon, MissingFrameIcon } from "@/components/icons/studio-icons";
import { cn } from "@/lib/utils";

import type { ShotCardData } from "./shot-card";

type StatusSpec = { label: string; icon: LucideIcon; iconClassName: string };

/**
 * 状态一律「图标/圆点 + 文字」双重编码，不靠颜色单独传达含义——
 * 与 `components/ui/status.tsx` 同一条规矩。
 *
 * 「待完善」用 rf 的 warning：站内语义层里没有通用的 warning 槽位，
 * 只有 `--running`（专指生成中），借它来画一个静态状态会误导后来的人。
 */
const SPEC: Record<ShotCardData["status"], StatusSpec> = {
  ready: { label: "可生成", icon: GateApprovedIcon, iconClassName: "text-success" },
  draft: { label: "待完善", icon: MissingFrameIcon, iconClassName: "text-rf-warning" },
  rendering: {
    label: "生成中",
    icon: Loader2,
    iconClassName: "animate-spin text-primary",
  },
  failed: { label: "失败", icon: X, iconClassName: "text-danger" },
};

export function ShotStatus({ status }: { status: ShotCardData["status"] }) {
  const spec = SPEC[status];
  const Icon = spec.icon;

  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] text-fg-muted">
      <Icon aria-hidden className={cn("size-3 shrink-0", spec.iconClassName)} />
      {spec.label}
    </span>
  );
}
