"use client";

import { useEffect, useRef, useState } from "react";
import { LayoutTemplate } from "lucide-react";

import { MOCK_TEMPLATES } from "@/lib/freeflow/mock-data";
import type { FreeflowTemplateCategory } from "@/lib/freeflow/types";
import { cn } from "@/lib/utils";

/**
 * 01 首页「推荐模板」网格（REQ-011）。
 *
 * 数据来自 `lib/freeflow/mock-data.ts`——**这一块没有真实接口**：
 * 后端没有模板表，也没有 `preset_graph` 这个概念（REQ-011 要的
 * `{id, name, category, thumbnail, preset_graph}` 一个字段都还不存在）。
 * 所以点模板卡不会真的建项目，只弹一句「即将支持」。
 *
 * 做成一个点了会建项目的按钮更糟：用户会以为自己拿到了那条预置流程，
 * 实际拿到的是一张空画布。
 */

const CATEGORIES: readonly (FreeflowTemplateCategory | "全部")[] = [
  "全部",
  "漫剧动画",
  "营销广告",
  "解说视频",
  "短剧",
];

const TOAST_MS = 2600;

export function HomeTemplateGrid() {
  const [category, setCategory] = useState<(typeof CATEGORIES)[number]>("全部");
  const [toast, setToast] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 组件卸载时把定时器清掉，否则切页之后还会 setState
  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  function notify(name: string) {
    setToast(`「${name}」：模板建项目即将支持，后端还没有预置节点图。`);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setToast(null), TOAST_MS);
  }

  const visible =
    category === "全部" ? MOCK_TEMPLATES : MOCK_TEMPLATES.filter((t) => t.category === category);

  return (
    <section>
      <div className="mb-2.5 flex items-baseline justify-between gap-3">
        <h2 className="text-base font-semibold text-fg">推荐模板</h2>
        <span className="text-xs text-fg-subtle">示例数据 · 不是后端内容</span>
      </div>

      <div className="mb-3 flex flex-wrap gap-1.5">
        {CATEGORIES.map((c) => (
          <button
            key={c}
            type="button"
            aria-pressed={category === c}
            onClick={() => setCategory(c)}
            className={cn(
              "inline-flex h-7 cursor-pointer items-center rounded-full px-2.5 text-xs",
              "transition-colors duration-150",
              category === c
                ? "bg-primary font-medium text-primary-fg"
                : "bg-surface-2 text-fg-muted hover:bg-surface-3 hover:text-fg",
            )}
          >
            {c}
          </button>
        ))}
      </div>

      {/* toast 的位置固定在网格上方，不做浮层——浮层要处理层级和焦点，
          这一句提示不值得那套东西 */}
      <p
        role="status"
        aria-live="polite"
        className={cn(
          "mb-2 rounded-md px-2.5 py-1.5 text-xs transition-opacity duration-150",
          toast ? "bg-primary-soft text-primary opacity-100" : "sr-only opacity-0",
        )}
      >
        {toast}
      </p>

      {visible.length === 0 ? (
        <p className="text-sm text-fg-subtle">这个分类下还没有示例模板。</p>
      ) : (
        <div className="grid grid-cols-2 items-start gap-2.5 sm:grid-cols-3 xl:grid-cols-6">
          {visible.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => notify(t.name)}
              title="模板建项目即将支持"
              className="flex cursor-pointer flex-col overflow-hidden rounded-lg border border-border bg-surface text-left transition-colors duration-150 hover:border-border-strong"
            >
              {/* 封面占位：模板没有 thumbnail 字段，纯色块 + 图标 */}
              <span className="flex aspect-[3/4] items-center justify-center bg-surface-3">
                <LayoutTemplate aria-hidden className="size-6 text-fg-subtle" />
              </span>
              <span className="px-2 py-1.5">
                <span className="block truncate text-xs font-medium text-fg">{t.name}</span>
                <span className="mt-0.5 block text-xs text-fg-subtle">{t.category}</span>
              </span>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
