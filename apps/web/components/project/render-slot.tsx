"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { StatusChip } from "@/components/ui/status";
import { assets as assetsApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { RenderSubject, Renders, RenderView } from "@/lib/useRenders";

/**
 * 出图缩略图。
 *
 * 链接是预签名的、有有效期，所以只能在渲染时现签，不能提前塞进列表接口
 * ——资产库那边也是这么做的，两处必须是同一套取图方式。
 */
export function RenderThumb({ assetId, alt }: { assetId: string; alt: string }) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    assetsApi
      .downloadUrl(assetId)
      .then((r) => alive && setUrl(r.url))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [assetId]);

  if (!url) return <div className="size-full animate-pulse bg-surface-3" />;

  return (
    <a href={url} target="_blank" rel="noreferrer" className="block size-full">
      {/* eslint-disable-next-line @next/next/no-img-element -- 预签名 URL 是运行时才知道的外部地址，用不了 next/image 的构建期优化 */}
      <img src={url} alt={alt} className="size-full object-cover" />
    </a>
  );
}

/**
 * 出图入口：一个占位框 + 一个按钮。
 *
 * 状态、进度全部来自 `tasks`（ADR-008），这里不维护第二份——
 * 所以"生成中"的进度条和任务中心里那条是同一个数字。
 */
export function RenderSlot({
  subject,
  renders,
  label,
  alt,
  className,
  aspect = "aspect-[3/4]",
}: {
  subject: RenderSubject;
  renders: Renders;
  /** 还没出过图时按钮上的字 */
  label: string;
  alt: string;
  className?: string;
  aspect?: string;
}) {
  const view: RenderView | null = renders.renderOf(subject);
  const pending = renders.isPending(subject);
  const active = view?.status === "queued" || view?.status === "running";
  const failed = view?.status === "failed" || view?.status === "cancelled";

  return (
    <div className={cn("flex shrink-0 flex-col gap-1", className)}>
      <div
        className={cn(
          "overflow-hidden rounded border border-border bg-surface-3",
          aspect,
        )}
      >
        {view?.assetId ? (
          <RenderThumb assetId={view.assetId} alt={alt} />
        ) : (
          <div className="flex size-full flex-col items-center justify-center gap-1 px-1">
            {active ? (
              <>
                <StatusChip status={view.status} compact />
                <div className="h-1 w-full overflow-hidden rounded-full bg-surface-2">
                  <div
                    role="progressbar"
                    aria-valuenow={view.progress}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-label="出图进度"
                    className="h-full bg-running transition-[width] duration-300"
                    style={{ width: `${view.progress}%` }}
                  />
                </div>
              </>
            ) : failed ? (
              <span className="text-center text-xs text-danger" title={view?.errorCode ?? ""}>
                失败
              </span>
            ) : (
              <span className="text-center text-xs text-fg-subtle">未出图</span>
            )}
          </div>
        )}
      </div>

      {failed && view ? (
        <Button
          size="sm"
          variant="secondary"
          disabled={pending}
          onClick={() => renders.retry(subject, view.taskId)}
        >
          {pending ? "提交中…" : "重试"}
        </Button>
      ) : (
        <Button
          size="sm"
          variant={view?.assetId ? "ghost" : "primary"}
          disabled={pending || active}
          onClick={() => renders.generate(subject)}
          // 真实上游调用，会扣 Credits——按钮上说清楚，不要让用户点完才知道
          title={view?.assetId ? "重新出一张，会再扣一次 Credits" : "真实出图，会扣 Credits"}
        >
          {pending ? "提交中…" : active ? "生成中…" : view?.assetId ? "重新生成" : label}
        </Button>
      )}
    </div>
  );
}
