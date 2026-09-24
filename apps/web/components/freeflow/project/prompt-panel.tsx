"use client";

import Link from "next/link";
import { useEffect, useId, useState } from "react";
import { Copy, FileText, Loader2, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import {
  generation,
  generationError,
  isPlaceholderModel,
  PLACEHOLDER_LABEL,
  type PromptDraft,
  type PromptKind,
} from "@/lib/freeflow/generation-api";
import { forgetPrompt, rememberPrompt } from "@/lib/freeflow/prepared-prompts";

const LABEL: Record<PromptKind, string> = {
  character: "角色立绘提示词", scene: "场景四视图提示词",
  shot_image: "首帧图片提示词", shot_video: "逐镜视频提示词",
};

/** 这份词准备好之后，在这个产品里能拿它做什么。写清楚，免得视频被当成能出片。 */
const AFTER: Record<PromptKind, string> = {
  character: "准备好之后，这一份就是下一次「生成基准立绘」实际送给模型的词。",
  scene: "准备好之后，这一份就是下一次「生成四视图参考图」实际送给模型的词。",
  shot_image: "准备好之后，这一份就是下一次「生成首帧图」实际送给模型的词。",
  shot_video: "视频生成尚未接入，这份词只能查看和复制，拿到外部视频工具里用。",
};

export function PromptText({ label, text }: { label: string; text: string | null }) {
  const [notice, setNotice] = useState("");
  useEffect(() => setNotice(""), [text]);
  return (
    <section className="min-w-0 space-y-2">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-medium text-fg">{label}</h3>
        {text && <Button size="sm" variant="ghost" onClick={async () => {
          try { await navigator.clipboard.writeText(text); setNotice(`已复制全文（${text.length} 字）`); }
          catch { setNotice("复制失败，请选中文本手动复制"); }
        }}><Copy aria-hidden className="size-3.5" />复制全文</Button>}
      </div>
      {text ? <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-[2px] border border-border bg-surface-2 p-4 font-sans text-sm leading-7 text-fg selection:bg-primary-soft">{text}</pre>
        : <p className="rounded-lg bg-surface-2 p-3 text-xs text-fg-subtle">未记录或未返回</p>}
      <p role="status" className="text-xs text-fg-muted">{notice}</p>
    </section>
  );
}

/**
 * 提示词面板。
 *
 * **打开只读，准备才调模型。** 展开面板走的是 GET，它不跑任何推理；
 * 底下那颗「准备提示词」是显式的一次文本生成，按项目的模型与计费规则算钱。
 *
 * 读到或准备出一份没过期的词之后，会把它的 `run_id` 记进
 * `prepared-prompts`——下一次出图就用**用户刚看过的这一份**，而不是后端
 * 另外现准备一份。看到的词和出的图因此是同一份；面板里读到的是过期的词
 * 时反过来忘掉它，出图退回自动准备，不拿一份注定被拒的 id 去撞后端。
 */
export function PromptPanel({ projectId, kind, subjectKey, disabled = false, disabledReason, onPrepared }: {
  projectId: string; kind: PromptKind; subjectKey: string; disabled?: boolean;
  /** 为什么现在不能准备（例如"镜头有未保存的改动"）。写在按钮的 title 上。 */
  disabledReason?: string;
  onPrepared?: (draft: PromptDraft) => void;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<PromptDraft | null>(null);
  const [instruction, setInstruction] = useState("");
  const [loading, setLoading] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const titleId = useId();

  useEffect(() => {
    setDraft(null); setInstruction(""); setError("");
    if (!open) return;
    const controller = new AbortController();
    setLoading(true);
    generation.prompt(projectId, kind, subjectKey, controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return;
        setDraft(value);
        // 读到什么就记什么：没有（null）或已过期都是"忘掉"，见 rememberPrompt。
        rememberPrompt(projectId, kind, subjectKey, value);
      })
      .catch((e) => {
        if (controller.signal.aborted) return;
        setError(generationError(e));
        // 读不出来就别声称"下次会用这一份"——它可能已经不存在了。
        forgetPrompt(projectId, kind, subjectKey);
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [projectId, kind, subjectKey, open, refresh]);

  async function prepare() {
    setPreparing(true); setError("");
    try {
      const value = await generation.prepare(projectId, kind, subjectKey, instruction.trim());
      setDraft(value);
      rememberPrompt(projectId, kind, subjectKey, value);
      onPrepared?.(value);
    } catch (e) { setError(generationError(e)); }
    finally { setPreparing(false); }
  }

  const placeholder = isPlaceholderModel(draft?.model_id);
  const usable = Boolean(draft) && !draft?.stale;

  return <>
    <Button size="sm" variant="ghost" disabled={disabled}
      title={disabled ? disabledReason : "只查看已保存的提示词，打开不调用模型"}
      onClick={() => setOpen(true)}>
      <FileText aria-hidden className="size-3.5" />{kind === "shot_video" ? "视频提示词" : "查看提示词"}
    </Button>
    <Dialog open={open} onOpenChange={setOpen} labelledBy={titleId} dismissible={!preparing}
      placement="right" className="flex h-full w-full max-w-2xl flex-col overflow-hidden bg-surface shadow-2xl">
      <header className="border-b border-border p-5 pr-12">
        <p className="mb-1 text-xs text-fg-subtle">{kind.startsWith("shot_") ? `镜头 ${subjectKey}` : subjectKey}</p>
        <h2 id={titleId} className="text-lg font-semibold text-fg">{LABEL[kind]}</h2>
        <p className="mt-2 text-xs leading-5 text-fg-muted">
          {kind === "scene"
            ? "这份词用于一张 2×2 四视图概念图：俯视、主轴平视和两个对角机位共用同一份固定元素清单，画面里不出现人物。"
            : kind === "shot_video"
              ? "准备可复制的视频提示词。此操作不会生成视频。"
              : "查看本次准备的完整提示词，确认画面要求后再出图。"}
        </p>
      </header>
      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-5">
        {loading && <p role="status" className="flex items-center gap-2 text-sm text-fg-muted"><Loader2 className="size-4 animate-spin" />读取已保存的提示词…</p>}
        {error && <div role="alert" className="flex flex-wrap items-center gap-3 rounded-lg bg-danger-soft p-3 text-sm text-danger">{error}<Button size="sm" variant="ghost" disabled={preparing} onClick={() => setRefresh((n) => n + 1)}>重新读取</Button></div>}
        {!loading && !error && !draft && <div className="rounded-[2px] border border-dashed border-border p-6 text-sm leading-6 text-fg-muted">还没有准备提示词。系统会根据已保存的档案、镜头要求与项目画风生成，打开此面板不会调用模型。</div>}
        {draft && <>
          {draft.stale && <p role="status" className="rounded-lg bg-rf-warning/10 p-3 text-sm leading-6 text-fg">档案或镜头已修改，这份提示词已过期，不会被用于出图。请重新准备；已有图片会保留。</p>}
          {placeholder && <p role="status" className="rounded-lg bg-rf-warning/10 p-3 text-sm leading-6 text-fg"><strong className="font-medium">{PLACEHOLDER_LABEL}：</strong>这份词由占位模型产生，不是真实模型的输出。配置好文本模型后重新准备即可得到可用的提示词。</p>}
          {usable && kind !== "shot_video" && <p className="rounded-lg bg-primary-soft p-3 text-sm leading-6 text-fg">下一次出图会使用这一份提示词。</p>}
          <PromptText label="完整提示词" text={draft.prompt} />
          {draft.negative_prompt && <details><summary className="cursor-pointer text-sm text-fg-muted">避免出现的内容</summary><div className="mt-3"><PromptText label="负面提示词" text={draft.negative_prompt} /></div></details>}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-fg-subtle">
            <span>{draft.model_id || "模型未记录"}</span>
            {placeholder && <span className="rounded bg-rf-warning/15 px-1.5 py-0.5 text-fg">{PLACEHOLDER_LABEL}</span>}
            <span>{new Date(draft.created_at).toLocaleString()}</span>
          </div>
          <Link className="text-xs text-primary underline underline-offset-4" href={`/freeflow/projects/${projectId}/tasks?record_type=agent&record_id=${encodeURIComponent(draft.run_id)}`}>查看这次生成的记录</Link>
        </>}
        <div className="space-y-2 border-t border-border pt-5">
          <label htmlFor={`${titleId}-instruction`} className="text-sm font-medium text-fg">补充画面要求</label>
          <textarea id={`${titleId}-instruction`} value={instruction} onChange={(e) => setInstruction(e.target.value)}
            maxLength={2000} disabled={preparing} rows={3} placeholder="例如：保留角色手中的钥匙，背景不要出现其他人物。"
            className="w-full resize-y rounded-[2px] border border-border bg-bg p-3 text-sm text-fg outline-none focus:border-primary" />
          <p className="text-xs leading-5 text-fg-subtle">准备提示词会调用文本模型，使用当前项目的模型与计费规则。画风与角色身份沿用已确认的设定。{AFTER[kind]}</p>
        </div>
      </div>
      <footer className="flex items-center justify-between gap-3 border-t border-border p-4">
        <Button size="sm" variant="ghost" disabled={preparing} onClick={() => setOpen(false)}>关闭</Button>
        <Button variant="primary" disabled={loading || preparing || disabled} onClick={() => void prepare()}
          title={disabled ? disabledReason : undefined}>
          {preparing ? <Loader2 aria-hidden className="size-4 animate-spin" /> : <RefreshCw aria-hidden className="size-4" />}
          {preparing ? "正在准备提示词…" : draft ? "重新准备提示词" : "准备提示词"}
        </Button>
      </footer>
    </Dialog>
  </>;
}
