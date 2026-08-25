"use client";

import { Bell, Search } from "lucide-react";

/**
 * 全局层顶栏：搜索 + 通知 + 头像。搜索框原型里未定义要连什么后端
 * （没有 REQ 编号覆盖），本轮只做视觉，不接真实搜索——接了搜不出
 * 结果比不放更容易让人以为坏了。
 */
export function GlobalTopbar() {
  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-surface px-4">
      <div className="relative max-w-md flex-1">
        <Search
          aria-hidden
          className="absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-fg-subtle"
        />
        <input
          type="search"
          placeholder="搜索项目、模板、素材…"
          disabled
          title="原型阶段未接搜索"
          className="w-full rounded-lg border border-border bg-surface-2 py-1.5 pr-3 pl-8 text-sm text-fg placeholder:text-fg-subtle disabled:cursor-not-allowed"
        />
      </div>

      <button
        type="button"
        disabled
        title="原型阶段未接通知"
        className="rounded-lg p-2 text-fg-muted disabled:cursor-not-allowed"
      >
        <Bell aria-hidden className="size-4" />
      </button>

      <div className="size-8 rounded-full bg-surface-3" aria-hidden />
    </header>
  );
}
