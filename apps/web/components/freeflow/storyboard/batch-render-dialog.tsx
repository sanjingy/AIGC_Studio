// 视觉来自 ReelFlow 原型，数据由页面注入
"use client";

import * as React from "react";
import { Aperture, Coins, Images, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogCloseButton } from "@/components/ui/dialog";

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
 */
export function BatchRenderDialog(props: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  shotCount: number;
  estimateCredits?: number;
  estimateRange?: { low: number; high: number };
  onConfirm: () => Promise<void>;
}) {
  const { open, onOpenChange, shotCount, estimateCredits, estimateRange, onConfirm } = props;
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
      className="w-full max-w-lg overflow-hidden rounded-2xl border border-border bg-surface-3 text-fg shadow-rf-card"
    >
      <header className="flex items-start gap-3 border-b border-border px-6 py-5">
        <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-primary-soft text-primary">
          <Aperture aria-hidden className="size-4" />
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
            className="text-[10px] font-semibold tracking-[0.16em] text-fg-subtle uppercase"
          >
            生成策略
          </p>
          <div className="mt-3 rounded-xl border border-primary bg-primary-soft p-4">
            <div className="flex items-center gap-3">
              <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-primary text-primary-fg">
                <Images aria-hidden className="size-4" />
              </span>
              <div className="min-w-0">
                <p className="text-sm font-semibold text-fg">逐镜出图</p>
                <p className="mt-0.5 text-xs text-fg-muted">每个镜头独立提交并保留对应关系</p>
              </div>
            </div>
          </div>
        </section>

        <dl className="divide-y divide-border rounded-xl border border-border bg-surface-2 px-4">
          <div className="flex items-center justify-between gap-4 py-3 text-xs">
            <dt className="text-fg-muted">镜头数</dt>
            <dd className="tnum font-semibold text-fg">{shotCount}</dd>
          </div>
          <EstimateRow credits={estimateCredits} range={estimateRange} />
        </dl>

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
