"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, FolderOpen, Upload } from "lucide-react";

import { AssetPicker } from "@/components/project/asset-picker";
import { Button } from "@/components/ui/button";
import { StatusChip } from "@/components/ui/status";
import { assets as assetsApi, IMAGE_ACCEPT } from "@/lib/api";
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
 * 出图入口：一个占位框 + 一组按钮。
 *
 * 状态、进度全部来自 `tasks`（ADR-008），这里不维护第二份——
 * 所以"生成中"的进度条和任务中心里那条是同一个数字。
 *
 * 基准图有三条来路，它们在这里刻意**不是**平级的三选一：
 *
 * - 主按钮永远是「AI 生成」。它是花钱的那条，把它埋进下拉里会让最常用
 *   也最需要看清代价的动作变成两次点击。
 * - 底下那颗「用已有图」带出另外两条：从资产库挑一张、从本地传一张。
 *   两条都不调用任何模型，一分钱不花，所以和上面那颗在视觉上分开。
 *
 * 角色和场景用的是同一个组件，所以两边的交互天然一致——一边弹层一边
 * 下拉的话，用户会觉得这是两个不同的产品。分镜没有基准图这个概念
 * （`base_*_asset_id` 长在角色/场景档案上），所以它只有主按钮。
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
  const slotError = renders.errorOf(subject);
  const active = view?.status === "queued" || view?.status === "running";
  const failed = view?.status === "failed" || view?.status === "cancelled";

  // 分镜出图没有"基准图"，`base_*_asset_id` 只长在角色和场景档案上
  const assignSubject = subject.kind === "shot" ? null : subject;
  // 用户自己钉的那张没有任务，重试不了也没什么可重试的——所以"能不能重试"
  // 问的是有没有 taskId，不是状态是不是 failed
  const retryTaskId = failed ? (view?.taskId ?? null) : null;

  return (
    <div className={cn("flex shrink-0 flex-col gap-1", className)}>
      <div
        className={cn(
          "relative overflow-hidden rounded border border-border bg-surface-3",
          aspect,
        )}
      >
        {view?.assetId ? (
          <>
            <RenderThumb assetId={view.assetId} alt={alt} />
            {view.source === "assigned" && (
              // 这张不是生成的，是用户自己给的。不标出来的话，"我这张图是
              // 哪来的、要不要重新生成"就只能靠回忆。
              <span className="absolute top-1 left-1 rounded bg-fg/70 px-1 py-0.5 text-[10px] leading-none font-medium text-bg">
                自选
              </span>
            )}
          </>
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

      {retryTaskId ? (
        <Button
          size="sm"
          variant="secondary"
          disabled={pending}
          onClick={() => renders.retry(subject, retryTaskId)}
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

      {assignSubject && (
        <BaseImageActions
          subject={assignSubject}
          renders={renders}
          pickerTitle={`选一张图作为${label.replace(/^生成/, "")}`}
          disabled={pending || active}
          triggerClassName="w-full"
        />
      )}

      {slotError && (
        // 角色/场景档案是在抽屉里看的，中栏顶部那条错误横幅被抽屉盖住了。
        // 不在这里再说一遍，用户看到的就是按钮弹回原样、什么也没发生。
        <p role="alert" className="text-xs break-words text-danger">
          {slotError}
        </p>
      )}
    </div>
  );
}

/**
 * 「用已有图」的两条路：从资产库挑一张、从本地传一张。
 *
 * 单独导出，因为这组入口有两个落点——中栏抽屉里的 `RenderSlot`，
 * 和右栏 `AssetPanel` 的角色卡片。两处出图本来就共用同一份 `renders`
 * 状态（点哪边都一样），入口却只有一边有的话，用户会以为右栏那个角色
 * "不支持用自己的图"。
 *
 * 自己写一个小浮层而不是引一个下拉库：整个仓库的 UI 层就只有 button /
 * field / panel / status / disclosure 五个自制件，为两个菜单项引一套
 * 依赖不划算。Esc 和点外面关闭是必须的，缺了会显得"关不掉"。
 */
export function BaseImageActions({
  subject,
  renders,
  pickerTitle,
  disabled,
  triggerClassName,
}: {
  /** 只有角色和场景有基准图；分镜传进来也没有意义，调用方自己判断 */
  subject: Extract<RenderSubject, { kind: "character" | "scene" }>;
  renders: Renders;
  pickerTitle: string;
  disabled: boolean;
  triggerClassName?: string;
}) {
  const [open, setOpen] = useState(false);
  const [picking, setPicking] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  /**
   * 菜单画在 body 上（portal），不画在按钮旁边。
   *
   * 右栏那张角色卡是 `overflow-hidden rounded-xl`——绝对定位的浮层会被它
   * 裁掉，只露出一条边。抽屉里也一样（它自己是个滚动容器）。所以位置只能
   * 自己算：拿触发器的视口坐标，用 fixed 定位画到最上层。
   *
   * 代价是滚动之后坐标会失效，所以滚动和改窗口大小时直接关掉它——
   * 一个跟不上按钮的浮层比没有更糟。
   */
  const [at, setAt] = useState<{ top: number; left: number } | null>(null);

  const MENU_W = 160;
  const MENU_H = 108;

  useLayoutEffect(() => {
    if (!open) {
      setAt(null);
      return;
    }
    const r = wrapRef.current?.getBoundingClientRect();
    if (!r) return;
    // 下面放不下就翻到上面去，别让菜单掉出视口
    const below = r.bottom + 4;
    const top = below + MENU_H > window.innerHeight ? Math.max(4, r.top - MENU_H - 4) : below;
    setAt({ top, left: Math.max(4, Math.min(r.right - MENU_W, window.innerWidth - MENU_W - 4)) });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      // 菜单在 portal 里，不在 wrapRef 下面——不额外判一次的话，
      // mousedown 会先把它卸掉，onClick 永远等不到
      if (wrapRef.current?.contains(t) || menuRef.current?.contains(t)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    const close = () => setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", close);
    // capture：滚动的是内层容器，事件不冒泡到 window
    window.addEventListener("scroll", close, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [open]);

  const item =
    "flex w-full cursor-pointer items-center gap-2 px-2.5 py-1.5 text-left text-xs text-fg " +
    "transition-colors duration-150 hover:bg-surface-2";

  return (
    <div ref={wrapRef} className="relative">
      <Button
        size="sm"
        variant="ghost"
        className={cn("h-6.5", triggerClassName)}
        disabled={disabled}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        title="用一张已有的图，不调用模型，不扣 Credits"
      >
        用已有图
        <ChevronDown aria-hidden className="size-3" />
      </Button>

      {open &&
        at &&
        createPortal(
          <div
            ref={menuRef}
            role="menu"
            style={{ top: at.top, left: at.left, width: MENU_W }}
            className="fixed z-50 overflow-hidden rounded-md border border-border bg-surface py-1 shadow-lg"
          >
            <button
              type="button"
              role="menuitem"
              className={item}
              onClick={() => {
                setOpen(false);
                setPicking(true);
              }}
            >
              <FolderOpen aria-hidden className="size-3.5 shrink-0 text-fg-subtle" />
              从资产库选择
            </button>
            <button
              type="button"
              role="menuitem"
              className={item}
              onClick={() => {
                setOpen(false);
                fileRef.current?.click();
              }}
            >
              <Upload aria-hidden className="size-3.5 shrink-0 text-fg-subtle" />
              本地上传
            </button>
            <p className="mt-0.5 border-t border-border px-2.5 pt-1.5 text-[10px] leading-tight text-fg-subtle">
              用自己的图不扣 Credits
            </p>
          </div>,
          document.body,
        )}

      {/* 走的是资产模块现成的三段式直传（`assets.upload`）：MIME 白名单、
          大小上限、容量配额全长在那条链路上，另开一套等于绕过它们。
          accept 只是选择器的过滤，说了算的仍然是后端。 */}
      <input
        ref={fileRef}
        type="file"
        accept={IMAGE_ACCEPT}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          // 先清空再上传：选了同一个文件两次时 change 不会再触发，
          // 用户会以为第二次点了没反应
          e.target.value = "";
          if (file) renders.assignFromFile(subject, file);
        }}
      />

      <AssetPicker
        open={picking}
        title={pickerTitle}
        onClose={() => setPicking(false)}
        onPick={(assetId) => {
          setPicking(false);
          renders.assign(subject, assetId);
        }}
      />
    </div>
  );
}
