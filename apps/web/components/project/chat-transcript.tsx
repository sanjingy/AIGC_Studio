"use client";

import { useEffect, useRef } from "react";

import type { ChatMessage } from "@/lib/api";

/**
 * 会话记录。
 *
 * 从 `ProjectComposer` 里搬出来的：新布局里输入框钉在栏底，记录跟产出
 * 一起在上面的滚动区里，两件事不再共用一个容器。
 *
 * 助手消息旁边挂的是后端 diff 出的实际改动，不是模型自述——模型说只改
 * 一处、diff 说改了七处时，用户要能一眼看见。
 *
 * 新消息进来要滚到它上面。产出折叠组在会话**下面**，中栏滚到底看到的是
 * 「Agent 运行」而不是刚回的那句——所以滚的是会话的末尾，不是容器的底部。
 */
export function ChatTranscript({ messages }: { messages: ChatMessage[] }) {
  const endRef = useRef<HTMLDivElement>(null);
  const count = messages.length;

  useEffect(() => {
    if (count > 0) endRef.current?.scrollIntoView({ block: "nearest" });
  }, [count]);

  if (count === 0) return null;

  return (
    <div className="flex flex-col gap-3.5">
      {messages.map((m) =>
        m.author === "user" ? (
          <div key={m.id} className="flex justify-end">
            <div className="max-w-[82%] rounded-[14px] rounded-br-[4px] bg-primary-soft px-3 py-2.5 text-sm leading-6 text-fg">
              {m.text}
            </div>
          </div>
        ) : (
          <div key={m.id} className="flex flex-col gap-2">
            <div className="max-w-[88%] rounded-[14px] rounded-bl-[4px] border border-border bg-surface px-3 py-2.5 text-sm leading-6 text-fg-muted">
              <span className="tnum mr-1.5 text-xs text-fg-subtle">v{m.revision}</span>
              {m.text}
            </div>
            {m.changed_fields.length > 0 && (
              <ul className="flex flex-wrap gap-1 pl-0.5">
                {m.changed_fields.map((f) => (
                  <li
                    key={f}
                    className="rounded-md bg-surface-2 px-1.5 py-0.5 text-xs text-fg-subtle"
                  >
                    {f}
                  </li>
                ))}
              </ul>
            )}
          </div>
        ),
      )}
      <div ref={endRef} />
    </div>
  );
}
