"use client";

import { useEffect, useRef, useState } from "react";
import { CornerDownLeft, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Panel, PanelHeader } from "@/components/ui/panel";
import {
  ApiRequestError,
  projects,
  type ChatMessage,
  type ReviseTarget,
} from "@/lib/api";

const TARGET_LABEL: Record<ReviseTarget, string> = {
  plot_index: "情节目录",
  screenplay: "剧本",
  characters: "角色",
  scenes: "场景",
  storyboard: "分镜",
};

/**
 * 阶段产出的聊天修订。
 *
 * 三条约束在 UI 上是看得见的：
 * - 产出仍是结构化的，聊天只是输入形式，所以这里不渲染自由文本正文，
 *   改完之后由上层面板重新渲染结构化产出
 * - 每轮带版本号，旧版留在 agent_runs 里
 * - **助手消息显示后端 diff 出的改动清单**，不是模型自述改了什么。
 *   模型说只改一处、diff 说改了七处时，用户要能一眼看见
 */
export function ReviseChat({
  projectId,
  available,
  onRevised,
}: {
  projectId: string;
  /** 当前已有产出、因而可被修订的阶段 */
  available: ReviseTarget[];
  onRevised: () => void | Promise<void>;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [target, setTarget] = useState<ReviseTarget | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    projects.conversation(projectId).then(setMessages).catch(() => {});
  }, [projectId]);

  // 默认改最靠后的那个阶段——用户刚看到的就是它
  useEffect(() => {
    setTarget((cur) => (cur && available.includes(cur) ? cur : (available.at(-1) ?? null)));
  }, [available]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages.length, busy]);

  async function send() {
    const text = draft.trim();
    if (!text || !target || busy) return;

    setBusy(true);
    setError(null);
    setDraft("");
    try {
      await projects.revise(projectId, target, text);
      setMessages(await projects.conversation(projectId));
      await onRevised();
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.error.user_message : "修改失败");
      setDraft(text); // 失败时把输入还回去，不让用户重打一遍
    } finally {
      setBusy(false);
    }
  }

  if (available.length === 0) {
    return (
      <Panel>
        <PanelHeader title="修改" meta="先生成一版才能改" />
        <p className="px-3 py-6 text-center text-sm text-fg-subtle">
          还没有产出。上面点「开始 / 继续」跑出第一版，之后就能在这里用一句话改。
        </p>
      </Panel>
    );
  }

  return (
    <Panel>
      <PanelHeader
        title="修改"
        meta="每次修改是一次真实 LLM 调用"
        action={
          <div className="flex gap-1" role="group" aria-label="修改哪个阶段">
            {available.map((r) => (
              <button
                key={r}
                type="button"
                aria-pressed={target === r}
                onClick={() => setTarget(r)}
                className={
                  target === r
                    ? "cursor-pointer rounded-md bg-primary-soft px-2 py-0.5 text-xs font-medium text-primary transition-colors duration-150"
                    : "cursor-pointer rounded-md px-2 py-0.5 text-xs text-fg-subtle transition-colors duration-150 hover:bg-surface-2 hover:text-fg"
                }
              >
                {TARGET_LABEL[r]}
              </button>
            ))}
          </div>
        }
      />

      <div className="flex max-h-[380px] flex-col gap-2.5 overflow-y-auto p-3">
        {messages.length === 0 && (
          <p className="py-4 text-center text-xs text-fg-subtle">
            试试「第 3 个节点漏了，补上」「主角改成女性，30 岁上下」「第 5 镜改成过肩镜头」
          </p>
        )}

        {messages.map((m) =>
          m.author === "user" ? (
            <div key={m.id} className="flex justify-end">
              <div className="max-w-[80%] rounded-md bg-primary-soft px-2.5 py-1.5 text-sm text-fg">
                {m.text}
              </div>
            </div>
          ) : (
            <div key={m.id} className="flex flex-col gap-1">
              <div className="max-w-[85%] rounded-md bg-surface-2 px-2.5 py-1.5 text-sm text-fg-muted">
                <span className="tnum mr-1.5 text-xs text-fg-subtle">
                  v{m.revision}
                </span>
                {m.text}
              </div>
              {m.changed_fields.length > 0 && (
                <ul className="flex flex-wrap gap-1 pl-1">
                  {m.changed_fields.map((f) => (
                    <li
                      key={f}
                      className="rounded bg-surface-2 px-1.5 py-0.5 text-xs text-fg-subtle"
                    >
                      {f}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ),
        )}

        {busy && (
          <div className="flex items-center gap-1.5 text-xs text-fg-subtle">
            <Loader2 aria-hidden className="size-3 animate-spin" />
            正在改 {target && TARGET_LABEL[target]}…
          </div>
        )}
        <div ref={endRef} />
      </div>

      {error && (
        <p role="alert" className="mx-3 mb-2 rounded-md bg-danger-soft px-2.5 py-1.5 text-xs text-danger">
          {error}
        </p>
      )}

      <div className="flex items-end gap-2 border-t border-border p-3">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            // Enter 发送，Shift+Enter 换行
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void send();
            }
          }}
          rows={2}
          disabled={busy}
          aria-label={`修改${target ? TARGET_LABEL[target] : ""}`}
          placeholder={`说说要怎么改${target ? TARGET_LABEL[target] : ""}，Enter 发送`}
          className="flex-1 resize-none rounded-md border border-border-strong bg-surface px-2.5 py-2 text-sm text-fg placeholder:text-fg-subtle disabled:opacity-45"
        />
        <Button variant="primary" size="sm" disabled={busy || !draft.trim()} onClick={send}>
          <CornerDownLeft aria-hidden className="size-3.5" />
          发送
        </Button>
      </div>
    </Panel>
  );
}
