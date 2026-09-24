import Link from "next/link";
import { LockKeyhole, type LucideIcon } from "lucide-react";

import {
  GateApprovedIcon,
  GatePendingIcon,
  StudioMarkIcon,
} from "@/components/icons/studio-icons";
import { cn } from "@/lib/utils";

import type { StageKey, StageState } from "./workbench-shell";

/** 阶段条点击后告诉工作台目标 hash 的事件名。`project-workbench.tsx` 监听它。 */
export const STAGE_HASH_EVENT = "ff:stage-hash";

type StateMeta = { label: string; icon: LucideIcon; tone: string };

/** 每个状态都带图标和文字，颜色只是第三重编码，去掉颜色也读得懂 */
const STATE_META: Record<StageState["state"], StateMeta> = {
  locked: { label: "已锁定", icon: LockKeyhole, tone: "text-fg-subtle" },
  ready: { label: "已就绪", icon: GatePendingIcon, tone: "text-fg-muted" },
  approved: { label: "已审核", icon: GateApprovedIcon, tone: "text-success" },
  active: { label: "当前阶段", icon: StudioMarkIcon, tone: "text-primary" },
  pending: { label: "待开始", icon: GatePendingIcon, tone: "text-fg-subtle" },
};

/**
 * 制作阶段的胶片连续带。每一格是一个**真实链接**，点了去看那一段的内容。
 *
 * 两种信息分开编码，不能混：
 * - **生产状态**（`data-state` + 图标 + 文字）：后端 `orchestrator._NEXT` 说了算。
 *   点击不推进、不审批、不解锁——锁定的格也能点，只是去**看**，不是去跑。
 * - **正在查看**（`aria-current="page"` + `data-viewing`）：当前路由落在哪一格。
 *   以前这里把 `aria-current="step"` 挂在生产的当前阶段上，读屏会把
 *   "你在看这页"和"生产走到这步"读成一件事。
 *
 * 为什么仍是一条连续带而不是一排卡片：制作流程本身连续，跳不过任何一格。
 * 帧与帧之间只有接片线，没有间距；齿孔沿上下边走，是这套界面唯一的纹理。
 */
export function StageRail({
  stages,
  viewing = null,
}: {
  stages: StageState[];
  viewing?: StageKey | null;
}) {
  const items = stages ?? [];
  if (items.length === 0) return null;

  return (
    <nav aria-label="制作阶段" className="ff-strip ff-strip-scroll">
      <ol className="ff-strip-track">
        {items.map((stage, index) => {
          const meta = STATE_META[stage.state];
          const Icon = meta.icon;
          const isViewing = stage.key === viewing;

          return (
            <li key={stage.key} data-state={stage.state} className="ff-frame">
              <Link
                href={stage.href}
                // 同一页里只换 hash（故事 ↔ 剧本）时 Next 走 pushState，浏览器不发
                // hashchange，而且 URL 何时更新不确定。直接把目标 hash 告诉工作台，
                // 它据此重算"正在查看"，不去猜时机。
                onClick={() => {
                  const hash = stage.href.includes("#") ? `#${stage.href.split("#")[1]}` : "";
                  window.dispatchEvent(new CustomEvent(STAGE_HASH_EVENT, { detail: hash }));
                }}
                aria-current={isViewing ? "page" : undefined}
                data-viewing={isViewing ? "true" : undefined}
                data-stage={stage.key}
                className="ff-frame-link"
              >
                <span className="ff-frame-no">{String(index + 1).padStart(2, "0")}</span>
                <span className="ff-frame-name">{stage.label}</span>
                <span className={cn("ff-frame-state", meta.tone)}>
                  <Icon aria-hidden className="size-3 shrink-0" />
                  {/* 生产状态用文字说，不借 aria-current：那个属性留给"正在查看" */}
                  <span className="sr-only">生产状态：</span>
                  {meta.label}
                </span>
                {isViewing && <span className="sr-only">（正在查看）</span>}
              </Link>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
