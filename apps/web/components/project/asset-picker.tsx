"use client";

import * as React from "react";
import { Check, ImageOff, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ApiRequestError, assets as assetsApi, type LibraryAsset } from "@/lib/api";
import { cn, formatBytes } from "@/lib/utils";

/**
 * 从资产库挑一张图。
 *
 * 只列图片：筛选交给后端的 `type` 参数（`assets.library({ type: "image" })`），
 * 不是拉全量再在前端过一遍——那样 limit 一到就会漏掉更早的图片，而用户
 * 恰恰会去翻更早的那些。
 *
 * 卡片的摆法照抄素材库（`freeflow/asset-library-grid.tsx` 的 `FileCard`）：
 * 同一批资产在两个地方长得不一样，用户会以为是两个库。缩略图同样是现签
 * 现取——预签名链接有有效期，存不住。
 *
 * 选中再确认，不做"点一下就生效"：这一步会**覆盖**当前的基准图，
 * 而基准图是后续所有镜头的一致性基准，误点的代价不对称。
 */
export function AssetPicker({
  open,
  title,
  onClose,
  onPick,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  onPick: (assetId: string) => void;
}) {
  const [rows, setRows] = React.useState<LibraryAsset[] | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [picked, setPicked] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  React.useEffect(() => {
    if (!open) return;
    let alive = true;
    setRows(null);
    setError(null);
    setPicked(null);
    assetsApi
      .library({ type: "image" })
      .then((d) => alive && setRows(d.assets))
      .catch((e) => {
        if (!alive) return;
        setRows([]);
        setError(e instanceof ApiRequestError ? e.error.user_message : "加载资产库失败");
      });
    return () => {
      alive = false;
    };
  }, [open]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <button
        type="button"
        aria-label="关闭"
        onClick={onClose}
        className="absolute inset-0 cursor-default bg-fg/25"
      />

      <div className="relative flex max-h-[80vh] w-[min(880px,100%)] flex-col rounded-[2px] border border-border bg-surface shadow-2xl">
        <div className="flex h-13 shrink-0 items-center gap-3 border-b border-border px-4">
          <h2 className="truncate text-sm font-semibold text-fg">{title}</h2>
          <span className="truncate text-xs text-fg-subtle">
            用自己的图不调用任何模型，不扣 Credits
          </span>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="ml-auto flex size-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-fg-muted transition-colors duration-150 hover:bg-surface-2 hover:text-fg"
          >
            <X aria-hidden className="size-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {error && (
            <p role="alert" className="mb-3 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
              {error}
            </p>
          )}

          {rows === null ? (
            <p className="py-10 text-center text-sm text-fg-subtle">加载中…</p>
          ) : rows.length === 0 ? (
            <p className="flex flex-col items-center gap-2 rounded-md border border-dashed border-border-strong px-4 py-10 text-center text-sm text-fg-subtle">
              <ImageOff aria-hidden className="size-6" />
              资产库里还没有图片。可以用「本地上传」直接传一张。
            </p>
          ) : (
            <div className="grid grid-cols-2 items-start gap-3 sm:grid-cols-4 md:grid-cols-5">
              {rows.map((a) => (
                <AssetCard
                  key={a.id}
                  asset={a}
                  selected={picked === a.id}
                  onSelect={() => setPicked(a.id)}
                />
              ))}
            </div>
          )}
        </div>

        <div className="flex h-13 shrink-0 items-center justify-end gap-2 border-t border-border px-4">
          <Button size="sm" variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button
            size="sm"
            variant="primary"
            disabled={!picked}
            onClick={() => picked && onPick(picked)}
          >
            用这张
          </Button>
        </div>
      </div>
    </div>
  );
}

function AssetCard({
  asset,
  selected,
  onSelect,
}: {
  asset: LibraryAsset;
  selected: boolean;
  onSelect: () => void;
}) {
  const [url, setUrl] = React.useState<string | null>(null);

  React.useEffect(() => {
    let alive = true;
    assetsApi
      .downloadUrl(asset.id)
      .then((r) => alive && setUrl(r.url))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [asset.id]);

  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={cn(
        "cursor-pointer overflow-hidden rounded-[2px] border text-left transition-colors duration-150",
        selected
          ? "border-primary ring-2 ring-primary/40"
          : "border-border hover:border-border-strong",
      )}
    >
      <div className="relative flex aspect-square items-center justify-center bg-surface-3">
        {url ? (
          // eslint-disable-next-line @next/next/no-img-element -- 预签名 URL 是运行时才知道的外部地址，用不了 next/image 的构建期优化
          <img src={url} alt={asset.filename} className="size-full object-cover" />
        ) : (
          <div className="size-full animate-pulse bg-surface-3" />
        )}
        {selected && (
          <span className="absolute top-1 right-1 flex size-5 items-center justify-center rounded-full bg-primary text-primary-fg">
            <Check aria-hidden className="size-3" />
          </span>
        )}
      </div>
      <div className="px-2 py-1.5">
        <p className="truncate text-xs text-fg" title={asset.filename}>
          {asset.filename}
        </p>
        <p className="tnum mt-0.5 text-xs text-fg-subtle">
          {asset.size_bytes === null ? "—" : formatBytes(asset.size_bytes)}
        </p>
      </div>
    </button>
  );
}
