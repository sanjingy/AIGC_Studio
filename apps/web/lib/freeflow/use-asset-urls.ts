"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { assets } from "@/lib/api";

/**
 * 一批资产 id → 可用的图片地址。
 *
 * 对象存储里的东西不能直接暴露，每张图都要现签一个预签名 URL
 * （`GET /assets/{id}/download-url`），而且**签出来的链接有有效期，存不住**。
 * 所以这里只在当前会话内按 id 缓存一次：换页重新签，页面开着太久时
 * 链接过期由 `ShotImage` 的 `onError` 兜底显示占位块，不会变成碎图。
 *
 * 为什么要成批：镜头墙一屏就有十几张图，每张自己 `useEffect` 去签，
 * 组件一重渲染就重签一轮，请求数按渲染次数涨。这里按 id 去重，
 * 同一个 id 全程只签一次。
 */
export function useAssetUrls(assetIds: readonly (string | null | undefined)[]) {
  const [urls, setUrls] = useState<Map<string, string>>(new Map());
  /** 已经发出去过的 id，避免重复签；失败的也记进来，不无限重试 */
  const requested = useRef<Set<string>>(new Set());

  // 依赖用排序后的字符串而不是数组本身：调用方每次渲染都会新建一个数组，
  // 直接依赖它等于每帧都跑一次 effect。
  const key = useMemo(
    () =>
      Array.from(new Set(assetIds.filter((id): id is string => Boolean(id))))
        .sort()
        .join(","),
    [assetIds],
  );

  useEffect(() => {
    const ids = key ? key.split(",") : [];
    const missing = ids.filter((id) => !requested.current.has(id));
    if (missing.length === 0) return;

    let alive = true;
    for (const id of missing) requested.current.add(id);

    void Promise.all(
      missing.map(async (id) => {
        try {
          const { url } = await assets.downloadUrl(id);
          return [id, url] as const;
        } catch {
          return null;
        }
      }),
    ).then((pairs) => {
      if (!alive) return;
      const ok = pairs.filter((p): p is readonly [string, string] => p !== null);
      if (ok.length === 0) return;
      setUrls((prev) => {
        const next = new Map(prev);
        for (const [id, url] of ok) next.set(id, url);
        return next;
      });
    });

    return () => {
      alive = false;
    };
  }, [key]);

  return (assetId: string | null | undefined) => (assetId ? urls.get(assetId) : undefined);
}
