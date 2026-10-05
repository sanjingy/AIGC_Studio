"use client";

import { useEffect, useId, useState } from "react";
import Link from "next/link";
import { Download, Loader2 } from "lucide-react";

import { Dialog, DialogCloseButton } from "@/components/ui/dialog";
import { ApiRequestError, assets as assetsApi, type LibraryAsset } from "@/lib/api";
import { FILTER_LABEL, clipText, previewKind, type AssetFilter } from "@/lib/freeflow/asset-scope";
import { formatBytes, timecode } from "@/lib/utils";

/**
 * 文件预览抽屉。地址每次打开现签（预签名有有效期）；签出来的地址带 `attachment`，
 * 所以「下载」就是打开它。图、视频、音频直接作 `src`；文本读前一段；
 * PDF / EPUB / JSON 不内嵌，只给下载。
 */
export function AssetPreview({
  asset,
  source,
  onClose,
}: {
  asset: LibraryAsset | null;
  source: string;
  onClose: () => void;
}) {
  const headingId = useId();
  const [url, setUrl] = useState<string | null>(null);
  const [urlError, setUrlError] = useState<string | null>(null);
  const [text, setText] = useState<{ text: string; clipped: boolean } | "loading" | "error" | null>(null);
  const kind = asset ? previewKind(asset) : "none";

  useEffect(() => {
    if (!asset) return;
    let alive = true;
    setUrl(null);
    setUrlError(null);
    setText(null);
    assetsApi
      .downloadUrl(asset.id)
      .then(async (r) => {
        if (!alive) return;
        setUrl(r.url);
        if (previewKind(asset) !== "text") return;
        setText("loading");
        try {
          const res = await fetch(r.url);
          if (!res.ok) throw new Error(String(res.status));
          const body = await res.text();
          if (alive) setText(clipText(body));
        } catch {
          if (alive) setText("error");
        }
      })
      .catch((e) => {
        if (!alive) return;
        setUrlError(e instanceof ApiRequestError ? e.error.user_message : "读取文件地址失败");
      });
    return () => {
      alive = false;
    };
  }, [asset]);

  return (
    <Dialog
      open={asset !== null}
      onOpenChange={(next) => !next && onClose()}
      labelledBy={headingId}
      placement="right"
      className="h-full w-[min(560px,100vw)] overflow-y-auto border-l border-border-strong bg-surface"
    >
      {asset && (
        <>
          <div className="sticky top-0 z-10 flex items-start justify-between gap-3 border-b border-border bg-surface px-4 py-3">
            <div className="min-w-0">
              <h2 id={headingId} className="truncate text-sm font-semibold text-fg" title={asset.filename}>
                {asset.filename}
              </h2>
              <p className="mt-0.5 text-xs text-fg-subtle">{FILTER_LABEL[asset.type as AssetFilter] ?? asset.type}</p>
            </div>
            <DialogCloseButton onClick={onClose} />
          </div>

          <div className="flex flex-col gap-4 p-4">
            <div className="flex min-h-40 items-center justify-center overflow-hidden rounded-[2px] border border-border bg-bg">
              {urlError ? (
                <p role="alert" className="p-4 text-sm text-danger">
                  {urlError}
                </p>
              ) : !url ? (
                <Loader2 aria-label="加载中" className="size-5 animate-spin text-fg-subtle" />
              ) : kind === "image" ? (
                // eslint-disable-next-line @next/next/no-img-element -- 预签名 URL 是运行时才知道的外部地址
                <img src={url} alt={asset.filename} className="max-h-[60vh] w-full object-contain" />
              ) : kind === "video" ? (
                <video src={url} controls preload="metadata" className="max-h-[60vh] w-full" aria-label={asset.filename} />
              ) : kind === "audio" ? (
                <audio src={url} controls preload="metadata" className="m-4 w-full" aria-label={asset.filename} />
              ) : kind === "text" ? (
                text === "loading" || text === null ? (
                  <Loader2 aria-label="正在读取文本" className="size-5 animate-spin text-fg-subtle" />
                ) : text === "error" ? (
                  <p className="p-4 text-sm text-fg-subtle">文本读取失败，可以下载后查看。</p>
                ) : (
                  <div className="w-full">
                    <pre className="max-h-[60vh] overflow-auto p-3 text-xs leading-5 whitespace-pre-wrap text-fg">{text.text}</pre>
                    {text.clipped && (
                      <p className="border-t border-border px-3 py-2 text-xs text-fg-subtle">只显示前 20000 字，完整内容请下载。</p>
                    )}
                  </div>
                )
              ) : (
                <p className="p-4 text-sm text-fg-subtle">
                  {asset.type === "text" ? "文件较大，不在页面里预览，请下载查看。" : "这种文件不能在页面里预览，请下载查看。"}
                </p>
              )}
            </div>

            <dl className="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-x-3 gap-y-2 text-xs">
              <dt className="text-fg-subtle">格式</dt>
              <dd className="code break-all text-fg-muted">{asset.mime_type}</dd>
              <dt className="text-fg-subtle">大小</dt>
              <dd className="tnum text-fg-muted">{asset.size_bytes === null ? "未知" : formatBytes(asset.size_bytes)}</dd>
              {asset.width && asset.height ? (
                <>
                  <dt className="text-fg-subtle">尺寸</dt>
                  <dd className="tnum text-fg-muted">
                    {asset.width}×{asset.height}
                  </dd>
                </>
              ) : null}
              {asset.duration_ms ? (
                <>
                  <dt className="text-fg-subtle">时长</dt>
                  <dd className="tnum text-fg-muted">{timecode(asset.duration_ms)}</dd>
                </>
              ) : null}
              <dt className="text-fg-subtle">所属项目</dt>
              <dd className="min-w-0 text-fg-muted">
                {asset.project_id && source !== "不在项目列表中" ? (
                  <Link href={`/freeflow/projects/${asset.project_id}/overview`} className="text-primary hover:underline">
                    {source}
                  </Link>
                ) : (
                  source
                )}
              </dd>
              <dt className="text-fg-subtle">入库时间</dt>
              <dd className="code text-fg-muted">{new Date(asset.created_at).toLocaleString("zh-CN")}</dd>
            </dl>

            <div className="flex justify-end">
              {url ? (
                <a href={url} target="_blank" rel="noopener noreferrer" className="ff-primary-button">
                  <Download aria-hidden className="size-4" />
                  下载
                </a>
              ) : (
                <span className="ff-quiet-button opacity-60" aria-disabled="true">
                  <Download aria-hidden className="size-4" />
                  下载
                </span>
              )}
            </div>
          </div>
        </>
      )}
    </Dialog>
  );
}
