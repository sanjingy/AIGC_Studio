// 视觉来自 ReelFlow 原型，数据由页面注入
"use client";

import * as React from "react";
import { Aperture } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * 镜头画面。卡片和详情页共用同一块，两处的「还没出图」必须长得一样——
 * 同一个镜头在列表里和详情里显示成两种空状态，用户会以为是两条数据。
 *
 * 加载失败**退回占位图**而不是留一个碎图标：镜头图走的是预签名地址，
 * 过期是常态而不是异常，碎图会被当成生成失败。占位块上另外给一句
 * 只有读屏能听见的说明，把「还没出图」和「图没加载出来」区分开。
 */
export function ShotImage({
  src,
  alt,
  className,
  iconClassName = "size-9",
}: {
  src?: string;
  alt: string;
  className?: string;
  iconClassName?: string;
}) {
  const [failed, setFailed] = React.useState(false);

  // 换了一张图就得给它一次机会，否则前一张失败会把后面每一张都判死
  React.useEffect(() => {
    setFailed(false);
  }, [src]);

  if (src && !failed) {
    return (
      // eslint-disable-next-line @next/next/no-img-element -- 预签名 URL 运行时才知道，用不了 next/image 的构建期优化
      <img
        src={src}
        alt={alt}
        loading="lazy"
        onError={() => setFailed(true)}
        className={cn("size-full object-cover", className)}
      />
    );
  }

  return (
    <div className="rf-shot-placeholder grid size-full place-items-center">
      <Aperture aria-hidden className={cn("text-fg-subtle", iconClassName)} />
      <span className="sr-only">{failed ? "镜头图像加载失败" : "暂无镜头图像"}</span>
    </div>
  );
}
