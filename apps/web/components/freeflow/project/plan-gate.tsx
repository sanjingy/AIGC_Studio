"use client";

import { useMemo, useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";

import { GatePendingIcon } from "@/components/icons/studio-icons";
import type { LockVariablesPatch, PlanGateSummary, StyleOption } from "@/lib/api";
import { useLockVariables } from "@/lib/freeflow/use-lock-variables";
import type { ProjectState } from "@/lib/freeflow/use-project-state";
import { cn } from "@/lib/utils";

import { GateActions } from "./production-actions";

/**
 * 门① 开拍前确认（ADR-037 第 2 条）。
 *
 * **一次停顿，四项决定，一次提交。** 四项之所以合成一道门，是因为它们
 * 在信息上同时可决——都只依赖原文与情节目录，不依赖彼此。拆成四道门
 * 就是让用户为同一批信息停四次。
 *
 * 为什么在故事页而不是弹窗：这道门要核对的情节目录**就在这一页下面**，
 * 几十条。模态框盖住它，用户只能凭记忆确认「有没有遗漏」——而那正是
 * 这一项唯一要回答的问题。
 *
 * 写的顺序是**先存锁定变量、再过门**（`beforeApprove`）。反过来的话门已经
 * 过了、画风还没落库，编排器会拿着空 `style_key` 一路跑到角色出图。
 * 两步之间失败也能重来：变量已经在库里，刷新回来看到的是填过的值。
 */

/** 这道门上能改的三项。第四项（情节目录）是产出，不在这张表上。 */
type Editable = "era" | "region" | "ethnicity" | "style_key" | "adaptation_mode";

const EDITABLE: Editable[] = ["era", "region", "ethnicity", "style_key", "adaptation_mode"];

/**
 * 改编模式两个选项各配一句区别。
 *
 * 文案压缩自后端 `_ADAPTATION_INSTRUCTIONS`——那两段是真的会进提示词的
 * 指令，界面上说的必须是同一件事。**这是整条流水线上唯一一个用户必须做的
 * 分支选择**，两个选项并排摆而不是做成一个开关：「关掉洗稿」和「打开改编」
 * 在用户脑子里不是同一句话。
 */
const ADAPTATION_COPY: Record<string, { label: string; detail: string }> = {
  adapt: {
    label: "改编",
    detail: "保留原著的角色名、地名、场景设定与台词内容，只把叙述性文字转成可拍的场次。",
  },
  rewrite: {
    label: "洗稿",
    detail: "保留情节骨架、冲突结构与节奏，角色名、地名与具体台词全部换成原创，且换名成体系。",
  },
};

export function PlanGate({ projectId, state }: { projectId: string; state: ProjectState }) {
  const lock = useLockVariables(projectId);
  const [edits, setEdits] = useState<Partial<Record<Editable, string>>>({});

  const summary = (state.pendingApproval?.payload_json?.summary ?? {}) as PlanGateSummary;
  const saved = lock.data;

  const valueOf = (field: Editable): string => edits[field] ?? saved?.[field] ?? "";
  const set = (field: Editable, value: string) => setEdits((prev) => ({ ...prev, [field]: value }));

  /**
   * 只把**真的改了**的字段发出去。后端每个字段都可选，含义是「这次不动它」
   * ——整份读出来再传回去，两个标签页同开就会互相覆盖。
   */
  const patch = useMemo<LockVariablesPatch>(() => {
    const out: LockVariablesPatch = {};
    for (const field of EDITABLE) {
      const next = edits[field];
      if (next !== undefined && next !== (saved?.[field] ?? "")) out[field] = next;
    }
    return out;
  }, [edits, saved]);

  /**
   * 画风目录优先取**这次读到的**那一份，而不是门摘要里冻着的那份：
   * 摘要是门打开那一刻的快照，而目录是运营内容，中间可能已经上下架过。
   */
  const styles: StyleOption[] = saved?.style_options ?? summary.style?.options ?? [];
  const modes = saved?.adaptation_options ?? summary.adaptation?.options ?? ["adapt", "rewrite"];

  const liveNodes = Array.isArray(state.output.plot_index?.nodes)
    ? state.output.plot_index.nodes.length
    : null;
  const gateNodes = summary.nodes_total ?? summary.nodes?.length ?? 0;
  // 门打开之后又返工过情节目录：摘要停在旧版，页面下面那份才是当前值。
  const nodesDrifted = liveNodes !== null && gateNodes > 0 && liveNodes !== gateNodes;

  const era = summary.era ?? {};
  const undetermined = Boolean(era.undetermined) || !valueOf("era") || !valueOf("ethnicity");

  const blocked = lock.loading
    ? "锁定变量还在读取"
    : !valueOf("style_key")
      ? "先选一个画风：它决定全片的画面质感，第一张图生成之后就冻结了"
      : null;

  return (
    <GateActions
      state={state}
      gate="plan"
      approveBlockedReason={blocked}
      // 确认和打回都先存：那三项是项目级的，打回重跑情节目录之后仍然有效。
      // 不存的话用户下次回到这道门，面对的又是一张空表单。
      beforeApprove={async () => {
        const ok = await lock.save(patch);
        if (ok) setEdits({});
        return ok;
      }}
    >
      <div className="mt-3 flex flex-col gap-3">
        {saved?.legacy_unconfirmed && <LegacyNotice />}

        {lock.error && (
          <p role="alert" className="rounded-md bg-danger-soft px-2.5 py-1.5 text-xs text-danger">
            {lock.error}
          </p>
        )}

        <Step
          n={1}
          title="情节目录"
          hint={`共 ${gateNodes} 个节点。要确认的是「覆盖范围」——有没有该拍的情节没进目录。`}
        >
          <p className="text-xs leading-5 text-fg-muted">
            {summary.logline ||
              String(state.output.plot_index?.logline ?? "（情节目录还没有一句话梗概）")}
            {summary.genre ? ` · ${summary.genre}` : ""}
          </p>
          <p className="mt-1.5 text-xs leading-5 text-fg-subtle">
            全部 {gateNodes} 条就在这一页下面的
            <a href="#plot-index" className="mx-1 text-primary underline underline-offset-2">
              情节目录
            </a>
            里，边看边核对。发现遗漏就在下面的意见里写清楚哪一段没进来，然后「打回重做」。
          </p>
          {nodesDrifted && (
            <p className="mt-1.5 flex items-start gap-1.5 text-xs leading-5 text-rf-agent">
              <AlertTriangle aria-hidden className="mt-px size-3.5 shrink-0" />
              这道门打开时是 {gateNodes} 条，页面上现在是 {liveNodes} 条——情节目录在门打开之后
              又改过，以页面上那份为准。
            </p>
          )}
        </Step>

        <Step
          n={2}
          title="时代背景与国籍人种"
          hint="系统按原文证据判定，你可以改。判错会让角色人种、服装与场景建筑全部错位，且一路传导到分镜。"
          tone="emphasis"
        >
          {undetermined && (
            <p className="mb-2 flex items-start gap-1.5 rounded-md bg-rf-warning-soft px-2.5 py-1.5 text-xs leading-5 text-rf-warning">
              <AlertTriangle aria-hidden className="mt-px size-3.5 shrink-0" />
              原文里没有足够证据，系统没敢定。留空的话后面每个阶段各自猜一次，同一部剧会出现
              两种时代。
            </p>
          )}
          <div className="grid gap-2.5 sm:grid-cols-3">
            <Field
              label="时代背景"
              value={valueOf("era")}
              placeholder="如：民国 / 现代 / 架空古代"
              disabled={lock.loading || lock.saving}
              onChange={(v) => set("era", v)}
            />
            <Field
              label="国别 / 地区"
              value={valueOf("region")}
              placeholder="如：中国 / 日本"
              disabled={lock.loading || lock.saving}
              onChange={(v) => set("region", v)}
            />
            <Field
              label="人种"
              value={valueOf("ethnicity")}
              placeholder="如：东亚"
              disabled={lock.loading || lock.saving}
              onChange={(v) => set("ethnicity", v)}
            />
          </div>
          {(era.evidence || saved?.era_evidence) && (
            <p className="mt-2 text-xs leading-5 text-fg-subtle">
              判定依据（原文证据）：{era.evidence || saved?.era_evidence}
            </p>
          )}
        </Step>

        <Step
          n={3}
          title="画风"
          hint="全片一套。第一张图生成之后就冻结了，那时再改要重出全部已生成的画面。"
        >
          {styles.length === 0 ? (
            <p className="text-xs text-fg-subtle">画风目录暂时读不到，稍后重试。</p>
          ) : (
            <div role="radiogroup" aria-label="画风" className="grid gap-2 sm:grid-cols-2">
              {styles.map((style) => (
                <Choice
                  key={style.key}
                  name="plan-gate-style"
                  checked={valueOf("style_key") === style.key}
                  disabled={lock.loading || lock.saving}
                  label={style.name}
                  detail={style.description}
                  onSelect={() => set("style_key", style.key)}
                />
              ))}
            </div>
          )}
        </Step>

        <Step
          n={4}
          title="改编 / 洗稿"
          hint="整条流水线上唯一一个必须由你做的分支选择，它决定剧本阶段怎么写。"
        >
          <div role="radiogroup" aria-label="改编模式" className="grid gap-2 sm:grid-cols-2">
            {modes.map((mode) => {
              const copy = ADAPTATION_COPY[mode];
              return (
                <Choice
                  key={mode}
                  name="plan-gate-adaptation"
                  checked={valueOf("adaptation_mode") === mode}
                  disabled={lock.loading || lock.saving}
                  label={copy?.label ?? mode}
                  detail={copy?.detail ?? "后端新增的模式，界面说明还没跟上。"}
                  onSelect={() => set("adaptation_mode", mode)}
                />
              );
            })}
          </div>
        </Step>

        <p className="flex items-center gap-1.5 text-xs leading-5 text-fg-subtle">
          {lock.saving && <Loader2 aria-hidden className="size-3.5 animate-spin" />}
          {Object.keys(patch).length > 0
            ? `${Object.keys(patch).length} 项改动会在点下面的按钮时一并保存：先存变量，再过门；存不成功就不过门。`
            : "以上三项已经是库里的值。确认通过前会再存一次，存不成功就不过门。"}
        </p>

        {lock.saveError && (
          <p
            role="alert"
            className="rounded-md bg-danger-soft px-2.5 py-1.5 text-xs leading-5 text-danger"
          >
            {lock.saveError}
          </p>
        )}
      </div>
    </GateActions>
  );
}

/**
 * 「历史项目，未经确认」。
 *
 * `origin === "migrated" && confirmed_at === null` 的项目，锁定变量是
 * ADR-037 上线时迁移**按缺省值补的**，从来没有人看过一眼。不标出来，
 * 用户会以为那是他自己选的。这条约束此前只由后端保证，界面不显示等于没有。
 */
export function LegacyNotice({ className }: { className?: string }) {
  return (
    <p
      className={cn(
        "flex items-start gap-2 rounded-md border border-rf-agent/25 bg-rf-agent-soft px-2.5 py-2 text-xs leading-5 text-rf-agent",
        className,
      )}
    >
      <GatePendingIcon aria-hidden className="mt-px size-3.5 shrink-0" />
      <span>
        <b className="font-semibold">历史项目，未经确认。</b>
        这些值是 ADR-037 上线时按缺省补的，没有人确认过——请当成「还没选」来核对，
        尤其是画风和人种。
      </span>
    </p>
  );
}

/** 门里的一项：序号 + 标题 + 一句为什么，然后是这一项的控件。 */
function Step({
  n,
  title,
  hint,
  tone,
  children,
}: {
  n: number;
  title: string;
  hint: string;
  /** `emphasis` 给判错代价最大的那一项，让它在四项里先被看到。 */
  tone?: "emphasis";
  children: React.ReactNode;
}) {
  return (
    <section
      className={cn(
        "rf-panel-card rounded-xl border p-3",
        tone === "emphasis" ? "border-rf-warning/35" : "border-border",
      )}
    >
      <div className="flex items-baseline gap-2">
        <span
          className={cn(
            "tnum grid size-5 shrink-0 place-items-center rounded-md text-[10px] font-semibold",
            tone === "emphasis"
              ? "bg-rf-warning-soft text-rf-warning"
              : "bg-primary-soft text-primary",
          )}
        >
          {n}
        </span>
        <h4 className="text-sm font-semibold text-fg">{title}</h4>
      </div>
      <p className="mt-1 pl-7 text-xs leading-5 text-fg-subtle">{hint}</p>
      <div className="mt-2.5 pl-7">{children}</div>
    </section>
  );
}

function Field({
  label,
  value,
  placeholder,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  placeholder: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <label className="flex min-w-0 flex-col gap-1">
      <span className="text-[10px] text-fg-subtle">{label}</span>
      <input
        type="text"
        value={value}
        disabled={disabled}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-md border border-border-strong bg-surface px-2 py-1.5 text-sm text-fg transition-colors duration-150 focus:border-primary focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
      />
    </label>
  );
}

/**
 * 一个单选项。用**真的 radio input** 而不是 aria-checked 的按钮：
 * 键盘方向键在同名分组里切换、读屏报「第 N 项，共 M 项」都是原生行为，
 * 自己实现一遍只会少几条。选中状态除了颜色还有边框和那颗圆点，
 * 不只靠颜色区分。
 */
function Choice({
  name,
  checked,
  disabled,
  label,
  detail,
  onSelect,
}: {
  name: string;
  checked: boolean;
  disabled: boolean;
  label: string;
  detail: string;
  onSelect: () => void;
}) {
  return (
    <label
      className={cn(
        "flex cursor-pointer items-start gap-2 rounded-lg border p-2.5 transition-colors duration-150",
        checked
          ? "border-primary/45 bg-primary-soft"
          : "border-border bg-surface hover:bg-surface-2",
        disabled && "cursor-not-allowed opacity-60",
      )}
    >
      <input
        type="radio"
        name={name}
        checked={checked}
        disabled={disabled}
        onChange={onSelect}
        className="mt-0.5 size-3.5 shrink-0 accent-[var(--primary)]"
      />
      <span className="min-w-0">
        <span className={cn("block text-sm font-medium", checked ? "text-primary" : "text-fg")}>
          {label}
        </span>
        <span className="mt-0.5 block text-xs leading-5 text-fg-subtle">{detail}</span>
      </span>
    </label>
  );
}
