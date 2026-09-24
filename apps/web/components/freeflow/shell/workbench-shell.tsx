// 视觉来自 ReelFlow 原型，数据由页面注入
"use client";

import * as React from "react";
import type { JSX } from "react";
import type { LucideIcon } from "lucide-react";

import { ContextDrawer } from "./context-drawer";
import { SidebarNav } from "./sidebar-nav";
import { WorkbenchHeader } from "./workbench-header";

export type StageKey = "story" | "assets" | "script" | "storyboard" | "generation";

export interface StageState {
  key: StageKey;
  label: string;
  state: "locked" | "ready" | "approved" | "active" | "pending";
  /**
   * 点这一段去哪看它的内容。由项目工作台统一生成（`stage-links.ts::stageHref`），
   * 组件和 CSS 不猜 project id。五段都有，锁定的也有——点击只是查看，不改生产状态。
   */
  href: string;
}

export interface NavItem {
  id: string;
  label: string;
  href: string;
  icon: LucideIcon;
  badge?: string | number;
  disabled?: boolean;
  disabledReason?: string;
}

/** 右栏从这个宽度起常驻，与下面的 `xl:` 类必须是同一个值 */
const ASIDE_PINNED_QUERY = "(min-width: 80rem)";

/**
 * 工作台外壳：左导航 + 顶栏 + 主内容 + 右栏。
 *
 * 壳只管版式和两个纯 UI 状态（侧栏折没折、窄屏抽屉开没开）。阶段、任务、
 * 一致性这些内容一律由页面通过 `aside` 传进来——执行状态的唯一真相在
 * `tasks` 上（ADR-008），壳里再存一份必然会和它对不上。
 *
 * `theme-reelflow` 打在最外层：这套配色是挂在该节点上的一组 CSS 变量，
 * 靠继承往下发，所以壳内的弹窗也不能 portal 到 body。
 */
export function WorkbenchShell(props: {
  project: { id: string; title: string; subtitle?: string; savedAgo?: string; shots?: number };
  navigation: NavItem[];
  utilityNavigation: NavItem[];
  activeHref: string;
  stages: StageState[];
  /** 正在查看的是哪一段（与生产状态无关），没有对应段时不传 */
  viewingStage?: StageKey | null;
  primaryAction?: {
    label: string;
    onClick: () => void;
    disabled?: boolean;
    loading?: boolean;
  };
  aside?: React.ReactNode;
  children: React.ReactNode;
}): JSX.Element {
  const { project, activeHref, primaryAction, aside, children } = props;
  // 契约上这几项是必填的，这里仍然兜一层：页面从接口拿数据，
  // 一次失败的请求就可能把 undefined 传进来，壳不该跟着一起白屏
  const navigation = props.navigation ?? [];
  const utilityNavigation = props.utilityNavigation ?? [];
  const stages = props.stages ?? [];

  const [sidebarCollapsed, setSidebarCollapsed] = React.useState(false);
  const [asideOpen, setAsideOpen] = React.useState(false);
  const asideId = React.useId();

  // 宽屏下右栏是常驻的，抽屉必须收掉：留着它，焦点陷阱会锁在一块
  // 被 CSS 盖住的面板上，键盘再也 Tab 不出去
  React.useEffect(() => {
    const media = window.matchMedia(ASIDE_PINNED_QUERY);
    const sync = () => {
      if (media.matches) setAsideOpen(false);
    };
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);

  return (
    <div className="theme-reelflow flex h-full min-h-0 w-full overflow-hidden bg-bg text-fg">
      <SidebarNav
        navigation={navigation}
        utilityNavigation={utilityNavigation}
        activeHref={activeHref}
        collapsed={sidebarCollapsed}
        onCollapsedChange={setSidebarCollapsed}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <WorkbenchHeader
          project={project}
          stages={stages}
          viewingStage={props.viewingStage ?? null}
          primaryAction={primaryAction}
          asideToggle={
            aside
              ? { controls: asideId, open: asideOpen, onOpen: () => setAsideOpen(true) }
              : undefined
          }
        />

        <div className="relative flex min-h-0 flex-1">
          <main className="ff-workbench-content min-w-0 flex-1 overflow-y-auto bg-bg">{children}</main>

          {aside && (
            <>
              <aside
                aria-label="项目上下文"
                className="ff-context-aside hidden shrink-0 flex-col border-l border-border xl:flex"
              >
                <div className="min-h-0 flex-1 overflow-y-auto p-4">{aside}</div>
              </aside>
              <ContextDrawer id={asideId} open={asideOpen} onOpenChange={setAsideOpen}>
                {aside}
              </ContextDrawer>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
