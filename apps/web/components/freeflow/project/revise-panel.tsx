"use client";

import { useState } from "react";
import { Loader2, MessageSquareText, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { ReviseTarget } from "@/lib/api";
import { ROLE_LABEL, type ProjectState } from "@/lib/freeflow/use-project-state";
import { cn } from "@/lib/utils";

/**
 * 自然语言返工（`POST /projects/{id}/revise`）。
 *
 * 走的是产出这一阶段的同一个 Agent、同一个 output_schema——聊天只是输入
 * 形式，产出仍然是结构化的。**会扣 Credits**。
 *
 * 对话记录来自 `GET /projects/{id}/conversation`，不是本地拼的：刷新之后
 * 还要能看到自己上次说了什么、后端 diff 出实际改了哪些字段。
 */
export function RevisePanel({
  state,
  target,
  className,
}: {
  state: ProjectState;
  target: ReviseTarget;
  className?: string;
}) {
  const [instruction, setInstruction] = useState("");
  const messages = state.conversation.filter((m) => m.target_role === target);
  const working = state.busy === "revise";
  const stale = state.staleRoles.includes(target);

  if (!state.output[target]) return null;

  return (
    <section
      aria-label={`${ROLE_LABEL[target]}返工`}
      className={cn("rounded-xl border border-border bg-surface p-3.5", className)}
    >
      <div className="flex items-center gap-2">
        <MessageSquareText aria-hidden className="size-4 text-fg-muted" />
        <h3 className="text-sm font-semibold text-fg">用一句话改{ROLE_LABEL[target]}</h3>
        {stale && (
          <span className="ml-auto inline-flex items-center gap-1 rounded-full border border-running/25 bg-running-soft px-2 py-0.5 text-[10px] text-running">
            <RefreshCw aria-hidden className="size-3" />
            上游已变
          </span>
        )}
      </div>

      <label className="mt-2.5 flex flex-col gap-1">
        <span className="sr-only">返工要求</span>
        <textarea
          rows={3}
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          maxLength={2000}
          placeholder={`例如：把第 2 集结尾改成悬念收束`}
          className="resize-none rounded-md border border-border-strong bg-bg px-2 py-1.5 text-sm leading-5 text-fg"
        />
      </label>

      <div className="mt-2 flex items-center gap-2">
        <Button
          size="sm"
          variant="primary"
          disabled={working || !instruction.trim()}
          onClick={() =>
            void state.revise(target, instruction.trim()).then((ok) => ok && setInstruction(""))
          }
        >
          {working ? (
            <Loader2 aria-hidden className="size-3.5 animate-spin" />
          ) : (
            <RefreshCw aria-hidden className="size-3.5" />
          )}
          {working ? "重写中…" : "提交返工"}
        </Button>
        <span className="text-xs text-fg-subtle">
          会重跑这一阶段的 Agent 并扣 Credits；下游产出会被标记为过期。
        </span>
      </div>

      {state.actionError && state.busy === null && (
        <p role="alert" className="mt-2 rounded-md bg-danger-soft px-2.5 py-1.5 text-xs text-danger">
          {state.actionError}
        </p>
      )}

      {messages.length > 0 && (
        <ol className="mt-3 flex flex-col gap-1.5 border-t border-border pt-3">
          {messages.map((m) => (
            <li key={m.id} className="text-xs leading-5">
              <span
                className={cn(
                  "mr-1.5 rounded-sm px-1 py-0.5 text-[10px]",
                  m.author === "user"
                    ? "bg-primary-soft text-primary"
                    : "bg-surface-2 text-fg-subtle",
                )}
              >
                {m.author === "user" ? "你" : `第 ${m.revision} 版`}
              </span>
              <span className="text-fg-muted">{m.text}</span>
              {m.changed_fields.length > 0 && (
                <span className="ml-1 text-fg-subtle">
                  （实际改动：{m.changed_fields.slice(0, 6).join("、")}
                  {m.changed_fields.length > 6 ? " 等" : ""}）
                </span>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
