"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  AlertTriangle,
  Check,
  CircleDashed,
  CircleDot,
  FolderOpen,
  KeyRound,
  LayoutGrid,
  ListChecks,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import {
  STAGE_STEPS,
  groupDomId,
  groupOfStep,
  type StageKey,
} from "@/components/project/stages";
import { SKILLS_CHANGED } from "@/components/project/skill-upload";
import { useWorkspace } from "@/components/shell/workspace";
import { orgSkills, type OrgSkill } from "@/lib/api";
import { cn } from "@/lib/utils";

type Item = { href: string; label: string; icon: LucideIcon };

// 只列已经接了真实接口的页面。放一堆点进去是假数据的入口，
// 比少放几个更糟——用户分不清哪些能用。
const NAV: Item[] = [
  { href: "/dashboard", label: "工作台", icon: LayoutGrid },
  { href: "/assets", label: "资产库", icon: FolderOpen },
  { href: "/tasks", label: "任务中心", icon: ListChecks },
  { href: "/settings/keys", label: "模型密钥", icon: KeyRound },
];

export function Sidebar() {
  const pathname = usePathname();
  const workspace = useWorkspace();

  return (
    <nav
      aria-label="主导航"
      className="flex w-[212px] shrink-0 flex-col overflow-y-auto border-r border-border bg-surface"
    >
      <ul className="flex flex-col gap-0.5 p-2.5">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = pathname.startsWith(href);
          return (
            <li key={href}>
              <Link
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm transition-colors duration-150",
                  active
                    ? "bg-primary-soft font-medium text-primary"
                    : "text-fg-muted hover:bg-surface-2 hover:text-fg",
                )}
              >
                <Icon aria-hidden className="size-4 shrink-0" />
                {label}
              </Link>
            </li>
          );
        })}
      </ul>

      {workspace && <FlowList stage={workspace.stage} />}

      <SkillLibrary />
    </nav>
  );
}

/**
 * 当前项目走到哪一步了。
 *
 * **只读**。阶段图钉死在后端 `orchestrator._NEXT` 里（ADR-008：执行状态
 * 只认后端），前端既不能重排也不能增删——所以这里没有拖拽把手，也没有
 * 「编排」按钮，那两个东西点下去无事发生比不放更糟。
 *
 * 每一步是个锚点，点了打开中栏对应的产出抽屉。用 `<a href="#...">` 而不是
 * 回调：外壳在项目页的上层，传不下去回调，锚点则天然可用。
 */
function FlowList({ stage }: { stage: StageKey }) {
  const stageIndex = STAGE_STEPS.findIndex((s) => s.key === stage);

  return (
    <>
      <div className="mx-2.5 h-px bg-border" />
      <div className="px-3.5 pt-3.5 pb-1.5">
        <span className="text-xs font-semibold tracking-wider text-fg-subtle">流程</span>
      </div>
      <ol className="flex flex-col gap-0.5 px-2.5 pb-3">
        {STAGE_STEPS.map((s, i) => {
          const done = i < stageIndex;
          const current = i === stageIndex;
          const group = groupOfStep(s.key);
          // 没走到、或压根没有产出可看的步骤（路线 / 完成）不做成链接
          const target = group !== null && i <= stageIndex ? `#${groupDomId(group)}` : null;

          const body = (
            <>
              {done ? (
                <Check aria-hidden className="size-3.5 shrink-0 text-success" />
              ) : current ? (
                <CircleDot aria-hidden className="size-3.5 shrink-0" />
              ) : (
                <CircleDashed aria-hidden className="size-3.5 shrink-0" />
              )}
              {s.label}
            </>
          );

          const tone = current
            ? "bg-primary-soft font-medium text-primary"
            : done
              ? "text-fg-muted"
              : "text-fg-subtle";

          return (
            <li key={s.key}>
              {target ? (
                <a
                  href={target}
                  aria-current={current ? "step" : undefined}
                  className={cn(
                    "flex items-center gap-2 rounded-lg px-2 py-1.5 text-xs transition-colors duration-150 hover:bg-surface-2",
                    tone,
                  )}
                >
                  {body}
                </a>
              ) : (
                <span
                  aria-current={current ? "step" : undefined}
                  className={cn("flex items-center gap-2 rounded-lg px-2 py-1.5 text-xs", tone)}
                >
                  {body}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </>
  );
}

/**
 * 技能库。
 *
 * 列出本 org 上传过的 Skill——**只是一份清单**。上传入口在输入框的
 * 附件菜单里（用户是在那儿"把东西带进来"的），这里只负责让人看见
 * 自己传过什么、校验过没过。
 *
 * ADR-026：本轮只做到"能传、能选"，**运行时没接线**，所以这里没有
 * 「启用」「切换到这条生产线」之类的按钮——那些点下去什么都不会发生。
 */
function SkillLibrary() {
  const [items, setItems] = useState<OrgSkill[] | null>(null);

  const load = useCallback(() => {
    orgSkills
      .list()
      .then((page) => setItems(page.items))
      // 没登录或后端没起来时静默留空：技能库不是主功能，
      // 为它在导航栏上弹一条错误没有意义
      .catch(() => setItems([]));
  }, []);

  useEffect(() => {
    load();
    // 输入框传完一份就广播一声，这里跟着刷新——两个组件隔着整棵树，
    // 为这一件事把状态提到根上不划算
    window.addEventListener(SKILLS_CHANGED, load);
    return () => window.removeEventListener(SKILLS_CHANGED, load);
  }, [load]);

  if (items === null || items.length === 0) return null;

  return (
    <>
      <div className="mx-2.5 h-px bg-border" />
      <div className="flex items-baseline gap-2 px-3.5 pt-3.5 pb-1.5">
        <span className="text-xs font-semibold tracking-wider text-fg-subtle">技能库</span>
        <span className="tnum ml-auto text-xs text-fg-subtle">{items.length}</span>
      </div>

      <ul className="flex flex-col gap-1 px-2.5 pb-3">
        {items.map((s) => {
          const bad = s.status !== "valid";
          return (
            <li
              key={s.id}
              className="rounded-lg border border-border bg-surface px-2 py-1.5"
              title={bad ? (s.validation_errors ?? "校验未通过") : (s.skill_id ?? undefined)}
            >
              <div className="flex items-center gap-1.5">
                {bad ? (
                  <AlertTriangle aria-hidden className="size-3 shrink-0 text-danger" />
                ) : (
                  <Wrench aria-hidden className="size-3 shrink-0 text-fg-muted" />
                )}
                <span className="min-w-0 flex-1 truncate text-xs font-medium text-fg">
                  {s.name}
                </span>
                <span className="shrink-0 text-xs text-fg-subtle">{s.version}</span>
              </div>
              <div className="mt-0.5 pl-4.5 text-xs text-fg-subtle">
                {bad ? (
                  <span className="text-danger">校验未通过</span>
                ) : (
                  `${s.stage_count} 阶段 · ${s.gates.length} 道门`
                )}
              </div>
            </li>
          );
        })}
      </ul>

      {/* ADR-026 要求界面如实标注，不能让用户以为传上去就生效了 */}
      <p className="px-3.5 pb-3 text-xs leading-5 text-fg-subtle">
        运行时尚未接线，当前生产流程仍走内置阶段图。
      </p>
    </>
  );
}
