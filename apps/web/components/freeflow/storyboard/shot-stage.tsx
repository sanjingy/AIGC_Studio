"use client";

import * as React from "react";
import { History, Laptop } from "lucide-react";

import type { RenderView } from "@/lib/freeflow/use-images";
import { cn } from "@/lib/utils";

import { ShotImage } from "./shot-image";

const STATUS: Record<string, string> = {
  queued: "排队中",
  running: "生成中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

function when(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "时间未知";
  return at.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function originOf(view: RenderView): string {
  if (view.source === "assigned") return "手动指定";
  return view.imageSource === "local" ? "本机" : "平台";
}

/**
 * 单镜的画面区：大预览 + 出图动作 + 历史。
 *
 * 预览默认显示**当前版**（最新一张成功的图）。点历史里的某一张只是临时
 * 换一张看，不改变当前版——后端没有"设为当前"的接口（ADR-033 候选表未建），
 * 做成一颗按钮就是假入口。所以预览上方始终写明正在看的是哪一张。
 */
export function ShotStage({
  code,
  title,
  current,
  history,
  urlOf,
  actions,
  status,
}: {
  code: string;
  title: string;
  /** 当前版：最新一条有图的记录 */
  current: RenderView | null;
  /** 全部出图记录，新在前 */
  history: RenderView[];
  urlOf: (assetId: string | null | undefined) => string | undefined;
  actions: React.ReactNode;
  /** 动作下方的状态与错误行 */
  status?: React.ReactNode;
}) {
  const [peek, setPeek] = React.useState<string | null>(null);
  const peeked = peek ? history.find((h) => h.assetId === peek) ?? null : null;
  const shown = peeked ?? current;

  // 换镜头或当前版变了，回到当前版
  React.useEffect(() => setPeek(null), [code, current?.assetId]);

  const withImage = history.filter((h) => h.assetId);

  return (
    <section aria-label={`${code} 画面`} className="flex min-w-0 flex-col gap-3">
      <div className="relative aspect-video w-full overflow-hidden rounded-md border border-border bg-surface">
        <ShotImage src={urlOf(shown?.assetId)} alt={`${code} ${title}`} iconClassName="size-10" />
        {shown && (
          <span className="absolute top-2 left-2 rounded-sm bg-bg/85 px-1.5 py-0.5 text-xs text-fg-muted">
            {peeked ? `历史版本 · ${when(shown.createdAt)}` : `当前版 · ${when(shown.createdAt)}`}
          </span>
        )}
        {peeked && (
          <button
            type="button"
            onClick={() => setPeek(null)}
            className="absolute top-2 right-2 rounded-sm bg-bg/85 px-2 py-0.5 text-xs text-primary hover:text-fg focus-visible:outline-2 focus-visible:outline-primary"
          >
            回到当前版
          </button>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">{actions}</div>
      {status}

      {history.length > 0 && (
        <div>
          <p className="flex items-center gap-1.5 text-xs text-fg-muted">
            <History aria-hidden className="size-3.5" />
            出图记录 {history.length} 条
            {withImage.length > 1 && <span className="text-fg-subtle">（点缩略图查看，当前版固定为最新一张成功的图）</span>}
          </p>
          <ul className="mt-2 flex gap-2 overflow-x-auto pb-1">
            {history.map((h, i) => {
              const active = h.assetId !== null && h.assetId === shown?.assetId;
              const label = `${when(h.createdAt)} · ${originOf(h)} · ${STATUS[h.status] ?? h.status}`;
              return (
                <li key={`${h.taskId ?? "assigned"}-${h.createdAt}-${i}`} className="shrink-0">
                  <button
                    type="button"
                    disabled={!h.assetId}
                    aria-pressed={active}
                    aria-label={`查看 ${label}`}
                    title={h.errorCode ? `${label}（${h.errorCode}）` : label}
                    onClick={() => setPeek(h.assetId === current?.assetId ? null : h.assetId)}
                    className={cn(
                      "block w-28 overflow-hidden rounded-md border text-left",
                      "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-primary",
                      "disabled:cursor-default",
                      active ? "border-primary" : "border-border hover:border-border-strong",
                    )}
                  >
                    <span className="block aspect-video bg-surface-2">
                      <ShotImage src={urlOf(h.assetId)} alt="" iconClassName="size-5" />
                    </span>
                    <span className="flex items-center gap-1 px-1.5 py-1 text-[11px] leading-4 text-fg-muted">
                      {h.imageSource === "local" && <Laptop aria-hidden className="size-3 shrink-0" />}
                      <span className="tnum truncate">{when(h.createdAt)}</span>
                      <span
                        className={cn(
                          "ml-auto shrink-0",
                          h.status === "failed" ? "text-danger" : h.status === "succeeded" ? "text-fg-subtle" : "text-running",
                        )}
                      >
                        {STATUS[h.status] ?? h.status}
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </section>
  );
}
