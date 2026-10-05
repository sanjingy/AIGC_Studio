"use client";

import { useEffect, useId, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AlertTriangle, ArrowRight, CirclePlus, Loader2, Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { ApiRequestError, credits, projects } from "@/lib/api";
import {
  SOURCE_MAX,
  TITLE_MAX,
  charCount,
  createOnlyWarning,
  startAvailability,
  startPlan,
  type StartPhase,
} from "@/lib/freeflow/home-start";
import { useLeaveGuard } from "@/lib/freeflow/use-leave-guard";
import { useNavigationGuard } from "@/lib/freeflow/use-navigation-guard";
import { cn, formatCredits } from "@/lib/utils";

const message = (cause: unknown, fallback: string) =>
  cause instanceof ApiRequestError ? cause.error.user_message : fallback;

/**
 * 首页的创作输入。两个动作分开（规则见 `lib/freeflow/home-start.ts`）：
 *
 * - 「只创建项目」只发 `POST /projects`，原文不保存；
 * - 「开始生产」先确认，再 create → advance，advance 会调用模型、扣 Credits。
 *
 * 原文在提交前只存在这一页，所以有内容时拦住整页离开和站内跳转。
 */
export function StartComposer() {
  const router = useRouter();
  const titleId = useId();
  const sourceId = useId();
  const [title, setTitle] = useState("");
  const [source, setSource] = useState("");
  const [createdId, setCreatedId] = useState<string | null>(null);
  const [phase, setPhase] = useState<StartPhase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [pendingLeave, setPendingLeave] = useState<(() => void) | null>(null);

  const draft = { title, source, createdId, phase };
  const { createOnly, start } = startAvailability(draft);
  const warning = createOnlyWarning(source);
  const chars = charCount(source.trim());
  const unsaved = source.trim() !== "" && !leaving;
  // 建好了项目、advance 却失败：此时才换成「进入项目 / 重试」。生成进行中不给离开的入口。
  const failedAfterCreate = createdId !== null && phase === "idle";

  useLeaveGuard(unsaved);
  useNavigationGuard(unsaved, (proceed) => setPendingLeave(() => proceed));

  function go(projectId: string) {
    setLeaving(true);
    router.push(`/freeflow/projects/${projectId}/overview`);
  }

  async function createOnlyNow() {
    if (!createOnly.enabled) return;
    setError(null);
    setPhase("creating");
    try {
      const project = await projects.create(title.trim());
      go(project.id);
    } catch (cause) {
      setError(`创建项目失败：${message(cause, "请稍后重试")}`);
      setPhase("idle");
    }
  }

  async function startNow() {
    setConfirming(false);
    setError(null);
    let projectId = createdId;
    if (startPlan({ createdId }).create) {
      setPhase("creating");
      try {
        projectId = (await projects.create(title.trim())).id;
        setCreatedId(projectId);
      } catch (cause) {
        setError(`创建项目失败，没有开始生产：${message(cause, "请稍后重试")}`);
        setPhase("idle");
        return;
      }
    }
    if (!projectId) return;
    setPhase("advancing");
    try {
      await projects.advance(projectId, source.trim());
      go(projectId);
    } catch (cause) {
      setError(`项目已创建，但开始生产失败：${message(cause, "请稍后重试")}。原文还在这里，可以重试或先进入项目。`);
      setPhase("idle");
    }
  }

  return (
    <section id="new" aria-labelledby={`${titleId}-h`} className="scroll-mt-6">
      <div className="mb-4">
        <h1 id={`${titleId}-h`} className="text-[clamp(22px,2vw,28px)] font-semibold tracking-tight text-fg">
          从一段故事开始
        </h1>
        <p className="mt-1.5 max-w-[60ch] text-sm leading-6 text-fg-subtle">
          粘贴小说原文或写一句创意。系统会依次产出剧本、角色、场景和分镜，每一步都等你确认。
        </p>
      </div>

      <div className="rounded-[2px] border border-border-strong bg-surface p-4 sm:p-5">
        <div className="flex flex-col gap-1.5">
          <label htmlFor={titleId} className="flex items-center justify-between gap-2 text-sm font-medium text-fg">
            <span>项目名</span>
            {createdId && <span className="text-xs font-normal text-success">已创建</span>}
          </label>
          <input
            id={titleId}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            disabled={createdId !== null || phase !== "idle"}
            maxLength={TITLE_MAX}
            placeholder="例如：第七夜"
            className="h-10 rounded-md border border-border-strong bg-bg px-3 text-sm text-fg placeholder:text-fg-subtle focus:border-primary disabled:opacity-70"
          />
        </div>

        <div className="mt-4 flex flex-col gap-1.5">
          <label htmlFor={sourceId} className="text-sm font-medium text-fg">
            小说原文或创意
          </label>
          <textarea
            id={sourceId}
            value={source}
            onChange={(e) => setSource(e.target.value)}
            disabled={phase !== "idle"}
            rows={6}
            aria-describedby={`${sourceId}-hint`}
            placeholder="把小说原文粘进来，或写一句创意。路线由系统判断，不用选。"
            className="min-h-32 resize-y rounded-md border border-border-strong bg-bg px-3 py-2 text-sm leading-6 text-fg placeholder:text-fg-subtle focus:border-primary disabled:opacity-70"
          />
          <p id={`${sourceId}-hint`} className="flex flex-wrap justify-between gap-x-4 gap-y-1 text-xs text-fg-subtle">
            <span>原文只在「开始生产」时提交；在那之前只留在这一页，离开或刷新就没了。</span>
            <span className={cn("tnum", chars > SOURCE_MAX && "text-danger")}>
              {chars.toLocaleString("zh-CN")} / {SOURCE_MAX.toLocaleString("zh-CN")} 字
            </span>
          </p>
        </div>

        {error && (
          <p role="alert" className="mt-4 rounded-md border border-danger/25 bg-danger-soft px-3 py-2 text-sm text-danger">
            {error}
          </p>
        )}

        {phase !== "idle" && (
          <p role="status" className="mt-4 flex items-center gap-2 rounded-md bg-surface-2 px-3 py-2 text-sm text-fg-muted">
            <Loader2 aria-hidden className="size-4 animate-spin" />
            {phase === "creating"
              ? "正在创建项目…"
              : "项目已创建，正在生成路线判断和情节目录，可能需要一两分钟。请不要关闭页面。"}
          </p>
        )}

        <div className="mt-4 flex flex-col gap-3 border-t border-border pt-4 md:flex-row md:items-end md:justify-between">
          <div className="min-w-0 text-xs leading-5 text-fg-subtle">
            {failedAfterCreate ? (
              <p>项目已经建好，重试只会再次开始生产，不会再建一个项目。</p>
            ) : (
              <>
                <p>
                  <strong className="font-medium text-fg-muted">只创建项目</strong>
                  ：只保存项目名，不保存原文，也不调用模型。
                </p>
                <p>
                  <strong className="font-medium text-fg-muted">开始生产</strong>
                  ：创建项目并提交原文，会调用模型、扣 Credits，开始前会再确认一次。
                </p>
                {warning && (
                  <p className="mt-1 flex items-start gap-1.5 text-running">
                    <AlertTriangle aria-hidden className="mt-0.5 size-3.5 shrink-0" />
                    {warning}
                  </p>
                )}
              </>
            )}
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
            {failedAfterCreate ? (
              <Link href={`/freeflow/projects/${createdId}/overview`} className="ff-quiet-button">
                进入项目
                <ArrowRight aria-hidden className="size-4" />
              </Link>
            ) : (
              <Button
                disabled={!createOnly.enabled}
                title={createOnly.reason ?? "只保存项目名，进入项目"}
                onClick={() => void createOnlyNow()}
              >
                {phase === "creating" && !confirming ? (
                  <Loader2 aria-hidden className="size-4 animate-spin" />
                ) : (
                  <CirclePlus aria-hidden className="size-4" />
                )}
                只创建项目
              </Button>
            )}
            <Button
              variant="primary"
              disabled={!start.enabled}
              title={start.reason ?? "确认后创建项目并开始生产"}
              onClick={() => setConfirming(true)}
            >
              <Play aria-hidden className="size-4" />
              {createdId ? "重试开始生产" : "开始生产"}
            </Button>
          </div>
        </div>
        {!start.enabled && start.reason && phase === "idle" && (
          <p className="mt-2 text-right text-xs text-fg-subtle">{start.reason}</p>
        )}
      </div>

      <StartConfirm
        open={confirming}
        title={title.trim()}
        chars={chars}
        retry={createdId !== null}
        onCancel={() => setConfirming(false)}
        onConfirm={() => void startNow()}
      />
      <LeaveConfirm
        open={pendingLeave !== null}
        onStay={() => setPendingLeave(null)}
        onLeave={() => {
          const proceed = pendingLeave;
          setPendingLeave(null);
          setLeaving(true);
          proceed?.();
        }}
      />
    </section>
  );
}

/** 开始生产前的确认：写清会发生什么、花什么钱。余额是真实读数，读不到就照说。 */
function StartConfirm({
  open,
  title,
  chars,
  retry,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  title: string;
  chars: number;
  retry: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const headingId = useId();
  const [balance, setBalance] = useState<number | null | "error">(null);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setBalance(null);
    credits
      .balance()
      .then((b) => alive && setBalance(b.balance))
      .catch(() => alive && setBalance("error"));
    return () => {
      alive = false;
    };
  }, [open]);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => !next && onCancel()}
      labelledBy={headingId}
      className="w-full max-w-md rounded-[2px] border border-border-strong bg-surface p-5 shadow-rf-card"
    >
      <h2 id={headingId} className="text-base font-semibold text-fg">
        {retry ? "重试开始生产？" : "开始生产？"}
      </h2>
      <ul className="mt-3 flex list-disc flex-col gap-1.5 pl-5 text-sm leading-6 text-fg-muted">
        <li>
          {retry ? "使用已创建的项目" : "创建项目"}「<span className="text-fg">{title}</span>」，提交{" "}
          <span className="tnum">{chars.toLocaleString("zh-CN")}</span> 字原文。
        </li>
        <li>立即调用模型做路线判断和情节目录，跑到第一道审核门（开拍前确认）才停。</li>
        <li>这是真实的模型调用，按实际用量扣 Credits。</li>
      </ul>
      <p className="mt-3 rounded-md bg-surface-2 px-3 py-2 text-xs text-fg-subtle">
        当前可用：
        {balance === null ? (
          "读取中…"
        ) : balance === "error" ? (
          "余额读取失败，可以在项目里查看"
        ) : (
          <span className="tnum text-fg">{formatCredits(balance)} Credits</span>
        )}
      </p>
      <div className="mt-5 flex justify-end gap-2">
        <Button onClick={onCancel}>取消</Button>
        <Button variant="primary" onClick={onConfirm}>
          <Play aria-hidden className="size-4" />
          确认开始生产
        </Button>
      </div>
    </Dialog>
  );
}

function LeaveConfirm({ open, onStay, onLeave }: { open: boolean; onStay: () => void; onLeave: () => void }) {
  const headingId = useId();
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => !next && onStay()}
      labelledBy={headingId}
      className="w-full max-w-sm rounded-[2px] border border-border-strong bg-surface p-5 shadow-rf-card"
    >
      <h2 id={headingId} className="text-base font-semibold text-fg">
        离开这一页？
      </h2>
      <p className="mt-2 text-sm leading-6 text-fg-muted">原文还没有提交，离开后不会保存。</p>
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="primary" onClick={onStay}>
          留在这里
        </Button>
        <Button onClick={onLeave}>放弃原文并离开</Button>
      </div>
    </Dialog>
  );
}
