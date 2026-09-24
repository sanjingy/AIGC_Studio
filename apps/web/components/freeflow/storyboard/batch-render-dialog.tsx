// 视觉来自 ReelFlow 原型，数据由页面注入
"use client";

import * as React from "react";
import { Coins, Laptop, Loader2 } from "lucide-react";

import { RenderImageIcon } from "@/components/icons/studio-icons";
import { Button } from "@/components/ui/button";
import { Dialog, DialogCloseButton } from "@/components/ui/dialog";
import type { ImageSource } from "@/lib/api";

/** 估算行：有区间显示区间，只有单值显示单值，都没有就不渲染这一行。 */
function EstimateRow({
  credits,
  range,
}: {
  credits?: number;
  range?: { low: number; high: number };
}) {
  const format = (n: number) => n.toLocaleString("zh-CN");
  let text: string | null = null;
  if (range) {
    text = `${format(range.low)} – ${format(range.high)}`;
  } else if (credits !== undefined) {
    text = format(credits);
  }
  if (text === null) return null;

  return (
    <div className="flex items-center justify-between gap-4 py-3 text-xs">
      <dt className="flex items-center gap-1.5 text-fg-muted">
        <Coins aria-hidden className="size-3.5 text-rf-agent" />
        Credits 估算
      </dt>
      <dd className="tnum font-semibold text-fg">{text}</dd>
    </div>
  );
}

/**
 * 批量出图的确认弹窗。
 *
 * 只有「逐镜出图」一种策略——每个镜头单独提交、单独留下任务记录，
 * 所以任何一镜失败都只需要重跑那一镜。没有宫格、没有视频。
 *
 * 提交中把弹窗钉住（`dismissible={false}`）：`onConfirm` 是一串真实的
 * 建任务请求，此时关掉界面，用户看不到它到底提交了几条。
 *
 * **来源与代价必须写在这里。** 这是整个产品里一次点击花钱最多的地方
 * （26 镜 × 一次出图），而"这批图是谁画的、花的是谁的钱"在别处都看不到。
 */
export function BatchRenderDialog(props: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  shotCount: number;
  /** 这一批走平台 API 还是用户自己电脑上的连接器。 */
  imageSource: ImageSource;
  /** 连接器自报的 provider 名（`codex`）。拿不到时按 Codex 说。 */
  runnerLabel?: string | null;
  estimateCredits?: number;
  estimateRange?: { low: number; high: number };
  onConfirm: () => Promise<void>;
}) {
  const {
    open,
    onOpenChange,
    shotCount,
    imageSource,
    runnerLabel,
    estimateCredits,
    estimateRange,
    onConfirm,
  } = props;
  const isLocal = imageSource === "local";
  const [submitting, setSubmitting] = React.useState(false);
  const [submitFailed, setSubmitFailed] = React.useState(false);
  const titleId = React.useId();
  const descriptionId = React.useId();

  React.useEffect(() => {
    if (!open) setSubmitFailed(false);
  }, [open]);

  async function handleConfirm() {
    setSubmitting(true);
    setSubmitFailed(false);
    try {
      await onConfirm();
      onOpenChange(false);
    } catch (error) {
      console.error("failed to submit batch render", error);
      setSubmitFailed(true);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      labelledBy={titleId}
      describedBy={descriptionId}
      dismissible={!submitting}
      overlayClassName="bg-rf-overlay backdrop-blur-sm"
      className="w-full max-w-lg overflow-hidden rounded-[2px] border border-border bg-surface-3 text-fg shadow-rf-card"
    >
      <header className="flex items-start gap-3 border-b border-border px-6 py-5">
        <span className="grid size-9 shrink-0 place-items-center rounded-[2px] bg-primary-soft text-primary">
          <RenderImageIcon aria-hidden className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 id={titleId} className="text-lg font-semibold tracking-tight text-fg">
            批量出图
          </h2>
          <p id={descriptionId} className="mt-1 text-xs leading-5 text-fg-muted">
            镜头将按顺序分别进入生成队列，结果仍可逐条检查。
          </p>
        </div>
        <DialogCloseButton disabled={submitting} onClick={() => onOpenChange(false)} />
      </header>

      <div className="space-y-5 p-6">
        <section aria-labelledby={`${titleId}-strategy`}>
          <p
            id={`${titleId}-strategy`}
            className="text-[11px] font-semibold text-fg-muted"
          >
            生成策略
          </p>
          <div className="mt-3 rounded-[2px] border border-primary bg-primary-soft p-4">
            <div className="flex items-center gap-3">
              <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-primary text-primary-fg">
                <RenderImageIcon aria-hidden className="size-4" />
              </span>
              <div className="min-w-0">
                <p className="text-sm font-semibold text-fg">逐镜出图</p>
                <p className="mt-0.5 text-xs text-fg-muted">每个镜头独立提交并保留对应关系</p>
              </div>
            </div>
          </div>
        </section>

        <dl className="divide-y divide-border rounded-[2px] border border-border bg-surface-2 px-4">
          <div className="flex items-center justify-between gap-4 py-3 text-xs">
            <dt className="text-fg-muted">镜头数</dt>
            <dd className="tnum font-semibold text-fg">{shotCount}</dd>
          </div>
          <div className="flex items-center justify-between gap-4 py-3 text-xs">
            <dt className="flex items-center gap-1.5 text-fg-muted">
              {isLocal && <Laptop aria-hidden className="size-3.5" />}
              出图来源
            </dt>
            <dd className="font-semibold text-fg">
              {isLocal ? `本机 ${runnerLabel ?? "Codex"}（试点）` : "平台模型"}
            </dd>
          </div>
          <EstimateRow credits={estimateCredits} range={estimateRange} />
        </dl>

        {/* 口径与 `image-source-picker.tsx`、`local_runtime.usage_limit` 必须一致：
            本机这条**额外**消耗用户自己的订阅额度，平台 Credits 仍按同价计费。
            写成"本机免费"会让这一屏成为整个产品里最贵的一次误解。 */}
        <p className="text-xs leading-5 text-fg-muted">
          {isLocal
            ? `这 ${shotCount} 张由你电脑上的 ${runnerLabel ?? "Codex"} 逐张生成，消耗你自己的订阅额度；平台 Credits 仍按与平台模型同价逐张计费（失败的那张不扣）。`
            : `这 ${shotCount} 张由平台模型逐张生成，逐张扣 Credits（失败的那张不扣）。`}
        </p>

        {submitFailed && (
          <p role="alert" className="text-xs text-danger">
            未能提交，请重试。
          </p>
        )}
      </div>

      <footer className="flex items-center justify-end gap-2 border-t border-border bg-surface-2 px-6 py-4">
        <Button variant="ghost" disabled={submitting} onClick={() => onOpenChange(false)}>
          取消
        </Button>
        <Button
          variant="primary"
          aria-busy={submitting || undefined}
          disabled={submitting}
          onClick={() => void handleConfirm()}
        >
          {submitting && <Loader2 aria-hidden className="size-4 animate-spin" />}
          加入生成队列
        </Button>
      </footer>
    </Dialog>
  );
}
