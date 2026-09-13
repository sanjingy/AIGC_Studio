"use client";

import type * as React from "react";
import { useState } from "react";
import { AlertTriangle, Check, Loader2, Play, Undo2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { GateName } from "@/lib/api";
import { GatePendingIcon } from "@/components/icons/studio-icons";
import {
  GATE_LABEL,
  GATE_REDO_ROLE,
  ROLE_LABEL,
  type ProjectState,
} from "@/lib/freeflow/use-project-state";
import { cn } from "@/lib/utils";

/**
 * 生产链路上的三个写库动作，全站只有这一份实现：
 * **推进生产**（`advance`）、**过门**（`approved`）、**打回重做**
 * （`changes_requested`）。
 *
 * 概览页放完整的一条（`ProductionBar`），故事页和分镜页只放对应那道门
 * （`GateActions`）——审批必须在看得见产出的地方能做，但按钮的行为
 * 必须是同一个，两处各写一份必然分叉。
 */

function busyIcon(on: boolean) {
  return on ? <Loader2 aria-hidden className="size-3.5 animate-spin" /> : null;
}

/**
 * 门的审批。只在这道门真的开着时出现——没有 pending 审核就什么都不画。
 *
 * 四道门共用这一份（ADR-037）。门的**正文**由调用方通过 `children` 注入：
 * 剧本门和分镜门的正文就是页面上那份产出本身，不用注入；门① 和门③ 要
 * 用户在这一屏里做决定（选画风、核对锚点），正文是它们自己的表单。
 * 按钮、措辞、报错位置四道门必须一模一样——「确认通过」和「打回重做」
 * 在用户眼里是同一个动作，各写一份必然分叉出第三种说法。
 */
export function GateActions({
  state,
  gate,
  className,
  children,
  beforeApprove,
  approveBlockedReason,
}: {
  state: ProjectState;
  gate: GateName;
  className?: string;
  /** 这道门的正文。不传就只有说明和两颗按钮。 */
  children?: React.ReactNode;
  /**
   * 过门**之前**必须先成功的一步，返回 `false` 就不过门。
   *
   * 门① 用它先写锁定变量：顺序必须是"先存变量、再过门"，反过来的话
   * 门已经过了、画风还没存，编排器会拿着空 `style_key` 往下跑。
   * 两步之间失败也能重来——变量已经落库，用户回到这一页看到的是他填过的值。
   *
   * 打回重做走同一条：那三项是项目级的，退回重跑情节目录之后仍然有效，
   * 不存的话用户下次回到门① 面对的又是一张空表单。
   */
  beforeApprove?: () => Promise<boolean>;
  /** 有值 = 还不能确认通过，这句话就是原因。打回重做不受影响。 */
  approveBlockedReason?: string | null;
}) {
  const [comment, setComment] = useState("");
  const open = state.pendingGate === gate;
  if (!open) return null;

  const working = state.busy === "approve" || state.busy === "reject";

  const act = async (decide: (comment?: string) => Promise<boolean>) => {
    if (beforeApprove && !(await beforeApprove())) return;
    // 失败时把意见留在框里：清掉的话用户得凭记忆重打一遍。
    if (await decide(comment.trim() || undefined)) setComment("");
  };

  return (
    <section
      aria-label={`${GATE_LABEL[gate]}`}
      className={cn("rounded-xl border border-primary/25 bg-primary-soft p-3.5", className)}
    >
      <h3 className="flex items-center gap-2 text-sm font-semibold text-primary">
        <GatePendingIcon aria-hidden className="size-4 shrink-0" />
        {GATE_LABEL[gate]}
      </h3>
      <p className="mt-1 text-xs leading-5 text-fg-muted">
        确认后编排器进入下一阶段，并在你下一次「推进生产」时真实调用模型、扣 Credits。
        打回重做会退回产出这批内容的阶段（{ROLE_LABEL[GATE_REDO_ROLE[gate]]}），
        需要你先用自然语言说明要改什么。
      </p>

      {children}

      <label className="mt-2.5 flex flex-col gap-1">
        <span className="text-xs font-medium text-fg">意见（可选，会记进审核记录）</span>
        <textarea
          rows={2}
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          maxLength={1000}
          placeholder="例如：第 3 集节奏太慢，删掉回忆段"
          className="resize-none rounded-md border border-border-strong bg-surface px-2 py-1.5 text-sm leading-5 text-fg"
        />
      </label>
      <div className="mt-2.5 flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="primary"
          disabled={working || Boolean(approveBlockedReason)}
          title={approveBlockedReason ?? "确认通过，编排器进入下一阶段"}
          onClick={() => void act(state.approve)}
        >
          {busyIcon(state.busy === "approve") ?? <Check aria-hidden className="size-3.5" />}
          确认通过
        </Button>
        <Button size="sm" disabled={working} onClick={() => void act(state.reject)}>
          {busyIcon(state.busy === "reject") ?? <Undo2 aria-hidden className="size-3.5" />}
          打回重做
        </Button>
        {approveBlockedReason && (
          <span className="text-xs text-rf-warning">{approveBlockedReason}</span>
        )}
      </div>
      {state.actionError && (
        <p role="alert" className="mt-2 rounded-md bg-danger-soft px-2.5 py-1.5 text-xs text-danger">
          {state.actionError}
        </p>
      )}
    </section>
  );
}

/**
 * 「推进生产」。
 *
 * 项目还没跑过任何一步时必须先给原始素材：后端把它存进
 * `current_state_json.source`，是整条生产链的依据。之后的每一步都从
 * state 拼输入，所以补充说明是可选的。
 */
export function AdvanceAction({ state, className }: { state: ProjectState; className?: string }) {
  const [input, setInput] = useState("");
  const [open, setOpen] = useState(false);
  const needsSource = state.needsSource;
  const working = state.busy === "advance";
  const atGate = state.pendingGate !== null;
  const showBox = needsSource || open;

  return (
    <section className={cn("rounded-xl border border-border bg-surface p-3.5", className)}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-fg">推进生产</h3>
          <p className="mt-0.5 text-xs text-fg-subtle">
            当前阶段：{state.stageLabel}
            {atGate && " · 先处理审核门才能继续"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {!needsSource && (
            <Button size="sm" variant="ghost" onClick={() => setOpen((v) => !v)}>
              {open ? "收起补充说明" : "补充说明"}
            </Button>
          )}
          <Button
            size="sm"
            variant="primary"
            disabled={working || atGate || state.stage === "done" || (needsSource && !input.trim())}
            title={
              atGate
                ? "有审核门等待处理，确认或打回后才能继续"
                : state.stage === "done"
                  ? "文本链路已经走完"
                  : needsSource && !input.trim()
                    ? "先粘贴小说原文或写一句创意"
                    : "一路跑到下一个审核门"
            }
            onClick={() => void state.advance(input.trim()).then((ok) => ok && setInput(""))}
          >
            {busyIcon(working) ?? <Play aria-hidden className="size-3.5" />}
            {working ? "生成中…" : needsSource ? "开始生产" : "推进到下一道门"}
          </Button>
        </div>
      </div>

      {showBox && (
        <label className="mt-2.5 flex flex-col gap-1">
          <span className="text-xs font-medium text-fg">
            {needsSource ? "原始素材（小说原文，或一句话创意）" : "补充说明（可选）"}
          </span>
          <textarea
            rows={needsSource ? 6 : 3}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            maxLength={20000}
            placeholder={
              needsSource
                ? "把小说原文粘进来，或写一句创意。路线由 Router 自己判断，不用选。"
                : "这一步要注意什么"
            }
            className="resize-none rounded-md border border-border-strong bg-bg px-2 py-1.5 text-sm leading-5 text-fg"
          />
          <span className="text-xs text-fg-subtle">
            {needsSource
              ? "落库后每一步都以它为依据；上限 20000 字，超出部分不会保存。"
              : "会作为本次推进的输入落库，之后的阶段仍从项目状态拼输入。"}
          </span>
        </label>
      )}

      <p className="mt-2 flex items-start gap-1.5 text-xs leading-5 text-fg-subtle">
        <AlertTriangle aria-hidden className="mt-px size-3.5 shrink-0" />
        这是真实的模型调用，会扣 Credits，并且一路跑到下一个审核门才停。
      </p>

      {state.actionError && state.busy === null && (
        <p role="alert" className="mt-2 rounded-md bg-danger-soft px-2.5 py-1.5 text-xs text-danger">
          {state.actionError}
        </p>
      )}
    </section>
  );
}
