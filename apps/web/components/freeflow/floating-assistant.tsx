"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { ArrowUp, Paperclip, Sparkles, Wrench, X, type LucideIcon } from "lucide-react";

import { projects } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 悬浮 AI 助手（需求文档「悬浮 AI 助手（跨全部 6 屏常驻）」一节）。
 *
 * 挂载点是 `app/freeflow/layout.tsx`——全局层和项目层两套子布局之上的
 * 那一层。这个位置是本组件存在的全部理由：App Router 在同一 layout 下
 * 切换子路由不会卸载它，所以展开/收起状态和对话历史用组件内部
 * `useState` 就能跨路由保留，不需要 Context、Zustand 或 sessionStorage。
 *
 * 由此推出一条不能违反的约束：**不许让路由影响这个组件的生命周期**。
 * 下面用了 `usePathname()`，但它只被读来取当前项目 id 做标题展示——
 * 不做 key、不做条件挂载。给它套一个随路由变化的 key 会强制重新挂载，
 * 对话历史当场清空，这个组件也就没有存在的意义了。
 *
 * 发消息只落本地 state：需求文档 §Interactions 原文写着「发送消息后的行为
 * （真实调用哪个 Agent、如何把"直接操作画布"落地）需要产品与后端一起
 * 定义」。`lib/api.ts` 里确实有 `projects.conversation()`，但那是主线
 * advance/revise 阶段流水的对话记录，按 `target_role` 组织，且发消息要走
 * `advance`/`revise`——真实 LLM 调用、真实扣 Credits。把一个语义还没定义的
 * 通用助手接到那上面，等于用户随口一句话就烧钱，所以这里不接。
 */

type Message = { id: string; author: "user" | "assistant"; text: string };

/** 后端还没有「助手可以直接操作画布/分镜」的能力，老实说，不假装在处理。 */
const PLACEHOLDER_REPLY = "AI 操作能力尚未接入，当前仅展示界面。";

const PROJECT_PATH = /^\/freeflow\/projects\/([^/]+)/;

export function FloatingAssistant() {
  const pathname = usePathname();
  // 只读值，不参与挂载判断——见文件头注释。
  const projectId = PROJECT_PATH.exec(pathname ?? "")?.[1] ?? null;

  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  /** 项目 id → 标题。取不到就退化成「跟随当前项目」，不编一个假标题。 */
  const [titles, setTitles] = useState<Record<string, string>>({});

  const seq = useRef(0);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const projectTitle = projectId ? (titles[projectId] ?? null) : null;

  // 标题只在面板展开时才去取：收起状态下这行字根本不显示，
  // 没理由每次进项目路由都打一发请求。
  useEffect(() => {
    if (!open || !projectId || titles[projectId] !== undefined) return;
    let cancelled = false;
    projects
      .get(projectId)
      .then((p) => {
        if (!cancelled) setTitles((prev) => ({ ...prev, [projectId]: p.title }));
      })
      .catch(() => {
        // 取不到就保持「跟随当前项目」。失败不写进缓存，下次展开再试。
      });
    return () => {
      cancelled = true;
    };
  }, [open, projectId, titles]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const send = useCallback(() => {
    const text = draft.trim();
    if (!text) return;
    // 递增计数器而不是 crypto.randomUUID()：id 只用来做 React key，
    // 不需要全局唯一，也不想依赖 secure context。
    const n = (seq.current += 2);
    setMessages((prev) => [
      ...prev,
      { id: `m${n - 1}`, author: "user", text },
      { id: `m${n}`, author: "assistant", text: PLACEHOLDER_REPLY },
    ]);
    setDraft("");
  }, [draft]);

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="打开 AI 助手"
        aria-expanded={false}
        className="fixed right-5 bottom-5 z-50 flex h-[46px] cursor-pointer items-center gap-2 rounded-full bg-primary pr-4 pl-3.5 text-sm font-semibold text-primary-fg shadow-lg transition-colors duration-150 hover:bg-primary-hover"
      >
        <Sparkles aria-hidden className="size-[17px]" />
        AI 助手
      </button>
    );
  }

  return (
    <div
      role="dialog"
      aria-labelledby="freeflow-assistant-title"
      className="fixed right-5 bottom-5 z-50 flex max-h-[min(560px,calc(100dvh-40px))] w-[360px] flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl"
    >
      <div className="flex h-11 shrink-0 items-center gap-2 border-b border-border px-3">
        <Sparkles aria-hidden className="size-[15px] shrink-0 text-primary" />
        <span id="freeflow-assistant-title" className="shrink-0 text-sm font-semibold text-fg">
          AI 助手
        </span>
        <span className="min-w-0 truncate text-xs text-fg-subtle">
          {projectTitle ?? "跟随当前项目"}
        </span>
        <button
          type="button"
          onClick={() => setOpen(false)}
          aria-label="关闭 AI 助手"
          className="ml-auto flex size-6 shrink-0 cursor-pointer items-center justify-center rounded-md text-fg-muted transition-colors duration-150 hover:bg-surface-2 hover:text-fg"
        >
          <X aria-hidden className="size-[15px]" />
        </button>
      </div>

      <div
        ref={listRef}
        role="log"
        aria-label="对话记录"
        aria-live="polite"
        className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto p-3"
      >
        {messages.map((m) =>
          m.author === "user" ? (
            <div key={m.id} className="flex justify-end">
              <div className="max-w-[85%] rounded-[12px_12px_3px_12px] bg-primary-soft px-2.5 py-2 text-xs leading-[19px] whitespace-pre-wrap text-fg">
                {m.text}
              </div>
            </div>
          ) : (
            <div
              key={m.id}
              className="max-w-[88%] rounded-[12px_12px_12px_3px] border border-border bg-surface px-2.5 py-2 text-xs leading-[19px] whitespace-pre-wrap text-fg-muted"
            >
              {m.text}
            </div>
          ),
        )}

        {/* 设计稿这里写的是「AI 能替你操作画布、分镜、素材，不只是聊天」。
            那是产品意图不是当前能力，照抄就成了假承诺，所以改成实话。 */}
        <p
          className={cn(
            "text-center text-[11px] leading-[17px] text-fg-subtle",
            messages.length === 0 ? "m-auto px-2" : "mt-0.5",
          )}
        >
          原型阶段：AI 还不能替你操作画布、分镜或素材，这里只展示对话界面。
          {messages.length === 0 && " 对话历史和展开状态会跟着你切换页面一起保留。"}
        </p>
      </div>

      <div className="shrink-0 px-3 pt-2.5 pb-3">
        <div className="rounded-[14px] border border-border-strong bg-surface px-2.5 pt-2 pb-1.5">
          <textarea
            ref={inputRef}
            rows={2}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              // Enter 发送，Shift+Enter 换行。输入法组字中的 Enter 不算
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send();
              }
            }}
            aria-label="AI 对话输入"
            placeholder="说说要怎么改，或粘贴新的正文。Enter 发送"
            className="block w-full resize-none bg-transparent text-sm text-fg outline-none placeholder:text-fg-subtle"
          />

          <div className="mt-1 flex flex-nowrap items-center gap-1.5">
            <ComposerChip icon={Paperclip} label="附件" />
            <ComposerChip icon={Wrench} label="传 Skill" />
            <span className="min-w-0 flex-1 truncate text-right text-[10px] text-fg-subtle">
              不会真的调用模型
            </span>
            <button
              type="button"
              onClick={send}
              disabled={draft.trim().length === 0}
              aria-label="发送"
              className="flex size-7 shrink-0 cursor-pointer items-center justify-center rounded-[9px] bg-primary text-primary-fg transition-colors duration-150 hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-45"
            >
              <ArrowUp aria-hidden className="size-3.5" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/** 附件 / 传 Skill：后端这轮没有对应能力，一律禁用 + 说明，不做假入口。 */
function ComposerChip({ icon: Icon, label }: { icon: LucideIcon; label: string }) {
  return (
    <button
      type="button"
      disabled
      title="原型阶段未接"
      className="inline-flex h-[26px] shrink-0 items-center gap-1 rounded-[7px] bg-surface-2 px-2 text-[11px] whitespace-nowrap text-fg-muted disabled:cursor-not-allowed disabled:opacity-60"
    >
      <Icon aria-hidden className="size-3" />
      {label}
    </button>
  );
}
