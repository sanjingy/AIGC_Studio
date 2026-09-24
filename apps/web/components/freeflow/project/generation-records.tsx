"use client";

import { useEffect, useId, useMemo, useState } from "react";
import { FileText, ImageIcon, Loader2, RefreshCw, Search } from "lucide-react";

import { RenderThumb } from "@/components/project/render-slot";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { generation, generationError, isPlaceholderModel, PLACEHOLDER_LABEL, type GenerationDetail, type GenerationRecord } from "@/lib/freeflow/generation-api";
import { PromptText } from "./prompt-panel";

const STATUS: Record<string, string> = {
  queued: "排队中", running: "生成中", succeeded: "已完成", failed: "失败", cancelled: "已取消",
};
const SUBJECT: Record<string, string> = {
  character: "角色", scene: "场景", shot: "镜头", shot_image: "镜头首帧", shot_video: "视频提示词",
};

function timeOf(value: string) { return new Date(value).toLocaleString(); }
function durationOf(row: GenerationRecord) {
  if (!row.finished_at) return "—";
  const seconds = Math.max(0, Math.round((Date.parse(row.finished_at) - Date.parse(row.created_at)) / 1000));
  return seconds >= 60 ? `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒` : `${seconds} 秒`;
}

export function GenerationRecords({ projectId }: { projectId: string }) {
  const [rows, setRows] = useState<GenerationRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [day, setDay] = useState("");
  const [selected, setSelected] = useState<{ type: GenerationRecord["record_type"]; id: string } | null>(null);
  const [detail, setDetail] = useState<GenerationDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const titleId = useId();

  useEffect(() => {
    setSelected(null); setDetail(null); setQuery(""); setDay(""); setFilter("all");
    const params = new URLSearchParams(window.location.search);
    const type = params.get("record_type"); const id = params.get("record_id");
    if ((type === "agent" || type === "image") && id) setSelected({ type, id });
  }, [projectId]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(""); setRows([]);
    generation.records(projectId, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setRows(value); })
      .catch((e) => { if (!controller.signal.aborted) setError(generationError(e)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [projectId, refresh]);

  useEffect(() => {
    setDetail(null); setDetailError("");
    if (!selected) return;
    const controller = new AbortController();
    setDetailLoading(true);
    generation.detail(projectId, selected.type, selected.id, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setDetail(value); })
      .catch((e) => { if (!controller.signal.aborted) setDetailError(generationError(e)); })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [projectId, selected, refresh]);

  const filtered = useMemo(() => rows.filter((row) => {
    if (filter === "failed" && !["failed", "cancelled"].includes(row.status)) return false;
    if (filter === "agent" || filter === "image") { if (row.record_type !== filter) return false; }
    if (day) {
      const d = new Date(row.created_at);
      const localDay = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      if (localDay !== day) return false;
    }
    const needle = query.trim().toLowerCase();
    return !needle || [row.title, row.subject_key, row.model_id, row.agent_id, row.error_code, row.id]
      .some((value) => value?.toLowerCase().includes(needle));
  }), [rows, query, filter, day]);

  // 这一页是「一本翻得动的账」，不是一墙卡片：同一栏的值要上下对得齐。
  // 版式走 MASTER §5 的 .ff-ledger；筛选属于这本账本身，所以跟在栏头后面，
  // 不在账本外面另起一块。
  return <section className="space-y-3">
    <div className="ff-ledger">
      <div className="ff-ledger-head">
        <h2>生成记录</h2>
        <span className="flex items-center gap-3 font-normal">
          <span className="tnum">{filtered.length} / {rows.length} 条</span>
          <Button size="sm" variant="ghost" disabled={loading} onClick={() => setRefresh((n) => n + 1)}><RefreshCw aria-hidden className="size-3.5" />刷新</Button>
        </span>
      </div>
      <p className="ff-ledger-note">回看创作过程、实际提示词和生成结果，找到每一次变化的来源。最近 100 条记录，时间按浏览器本地时区显示。</p>
      <div className="ff-ledger-row flex-wrap" data-form="true">
        <label className="flex min-w-48 flex-1 items-center gap-2 rounded-md bg-surface-2 px-3 py-1.5">
          <Search aria-hidden className="size-4 text-fg-subtle" />
          <input aria-label="搜索生成记录" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索角色、镜头、模型或错误" className="min-w-0 flex-1 bg-transparent text-sm text-fg outline-none" />
        </label>
        <select aria-label="记录类型" value={filter} onChange={(e) => setFilter(e.target.value)} className="rounded-md border border-border bg-bg px-3 py-1.5 text-sm text-fg">
          <option value="all">全部记录</option><option value="agent">文本与提示词</option><option value="image">图片生成</option><option value="failed">失败与取消</option>
        </select>
        <input aria-label="按本地日期筛选" title="按浏览器本地日期筛选" type="date" value={day} onChange={(e) => setDay(e.target.value)} className="tnum rounded-md border border-border bg-bg px-3 py-1.5 text-sm text-fg" />
      </div>
      {error && <p role="alert" className="ff-ledger-row text-sm text-danger">{error}</p>}
      {loading && <p role="status" className="ff-ledger-row justify-center gap-2 py-8 text-sm text-fg-muted"><Loader2 aria-hidden className="size-4 animate-spin" />正在读取生成记录…</p>}
      {!loading && !error && !filtered.length && <p className="ff-ledger-empty">{rows.length ? "没有符合筛选条件的记录" : "还没有生成记录，完成一次创作或出图后会显示在这里"}</p>}
      {filtered.map((row) => <button key={`${row.record_type}:${row.id}`} type="button" onClick={() => setSelected({ type: row.record_type, id: row.id })}
        className="ff-ledger-row w-full text-left focus-visible:outline-2 focus-visible:outline-primary">
        <span aria-hidden className="shrink-0 text-fg-subtle">{row.record_type === "image" ? <ImageIcon className="size-4" /> : <FileText className="size-4" />}</span>
        <span className="ff-ledger-val block min-w-0">
          <span className="block truncate text-sm font-medium text-fg">{row.title}</span>
          {/* 中间点串接换成并排的列：主语（角色/场景/镜头 + ref）在左，
              模型和来源各占一格，扫一列的时候同类的值上下对得齐。 */}
          <span className="mt-0.5 flex min-w-0 flex-wrap items-baseline gap-x-4 gap-y-0.5 text-xs text-fg-muted">
            {row.subject_kind && <span className="truncate">{SUBJECT[row.subject_kind] ?? row.subject_kind}{row.subject_key ? ` ${row.subject_key}` : ""}</span>}
            <span className="code truncate text-fg-subtle">{row.model_id || "模型未记录"}</span>
            {row.source && <span className="text-fg-subtle">{row.source === "local" ? "本机" : row.source === "api" ? "平台" : row.source}</span>}
          </span>
        </span>
        {/* 占位模型跑出来的那一条和真实结果在列表里长得一模一样，
            不标出来，用户回看时分不清"这版效果差"和"这版根本没调模型"。 */}
        {isPlaceholderModel(row.model_id) && <span className="shrink-0 rounded-[2px] bg-rf-warning/15 px-2 py-0.5 text-xs text-fg">{PLACEHOLDER_LABEL}</span>}
        <span className={`shrink-0 rounded-[2px] px-2 py-0.5 text-xs ${row.status === "failed" ? "bg-danger-soft text-danger" : row.status === "succeeded" ? "bg-primary-soft text-primary" : "bg-surface-3 text-fg-muted"}`}>{STATUS[row.status] || row.status}</span>
        <span className="ff-ledger-num hidden leading-4 sm:block">{timeOf(row.created_at)}<br />{durationOf(row)}</span>
      </button>)}
    </div>
    <Dialog open={selected !== null} onOpenChange={(open) => { if (!open) setSelected(null); }} labelledBy={titleId} placement="right" className="flex h-full w-full max-w-3xl flex-col overflow-hidden bg-surface shadow-2xl">
      <header className="border-b border-border p-5 pr-12"><p className="mb-1 text-xs text-fg-subtle">生成详情</p><h2 id={titleId} className="text-lg font-semibold text-fg">{detail?.title || "正在读取记录"}</h2></header>
      <div className="min-h-0 flex-1 space-y-6 overflow-y-auto p-5">
        {detailLoading && <p role="status" className="flex items-center gap-2 text-sm text-fg-muted"><Loader2 aria-hidden className="size-4 animate-spin" />读取详情…</p>}
        {detailError && <div role="alert" className="rounded-lg bg-danger-soft p-3 text-sm text-danger">{detailError}<Button size="sm" onClick={() => setRefresh((n) => n + 1)}>重试读取</Button></div>}
        {detail && <>
          <dl className="grid grid-cols-2 gap-4 rounded-[2px] bg-surface-2 p-4 text-sm">
            <div><dt className="text-xs text-fg-subtle">状态</dt><dd className="mt-1 text-fg">{STATUS[detail.status] || detail.status}</dd></div>
            <div><dt className="text-xs text-fg-subtle">实际模型</dt><dd className="mt-1 break-all text-fg">{detail.model_id || "未记录"}</dd></div>
            <div><dt className="text-xs text-fg-subtle">开始时间</dt><dd className="mt-1 text-fg">{timeOf(detail.created_at)}</dd></div>
            <div><dt className="text-xs text-fg-subtle">耗时</dt><dd className="mt-1 text-fg">{durationOf(detail)}</dd></div>
          </dl>
          {/* 醒目地放在成果上面，不折进技术详情：它说的是"这张图/这段文字
              不是真实模型产出的"，是判断成果本身时最先要知道的一件事。 */}
          {isPlaceholderModel(detail.model_id) && <p role="status" className="rounded-lg bg-rf-warning/10 p-3 text-sm leading-6 text-fg"><strong className="font-medium">{PLACEHOLDER_LABEL}：</strong>这条记录由占位模型（{detail.model_id}）产生，{detail.record_type === "image" ? "图片是带提示词指纹的占位图" : "文字不是真实模型的输出"}，不代表最终效果。配置好对应模型后重新生成即可。这次仍然照常计费。</p>}
          {detail.incomplete && <p role="status" className="rounded-lg bg-rf-warning/10 p-3 text-sm leading-6 text-fg">这条历史记录不完整，部分输入或输出可能未保存全文。这里展示存档内容，不会用当前设定补造当时的提示词。</p>}
          {detail.error_code && <p className="rounded-lg bg-danger-soft p-3 text-sm text-danger">失败原因：{detail.error_code}</p>}
          {/* contain 而不是 cover：场景那张是 2×2 四视图，裁掉的正是其中两格。 */}
          {detail.asset_ids.length > 0 && <div className="grid grid-cols-2 gap-3">{detail.asset_ids.map((id) => <div key={id} className="aspect-square overflow-hidden rounded-[2px] border border-border bg-surface-2"><RenderThumb assetId={id} alt="这次生成的图片" fit="contain" /></div>)}</div>}
          {detail.user_input && <details><summary className="cursor-pointer text-sm font-medium text-fg">查看本次输入</summary><div className="mt-3"><PromptText label="输入存档" text={detail.user_input} /></div></details>}
          <PromptText label={detail.record_type === "image" ? "实际提交的图片提示词" : "提示词存档"} text={detail.prompt} />
          {detail.negative_prompt && <PromptText label="负面提示词" text={detail.negative_prompt} />}
          {detail.record_type === "image" && <PromptText label="模型返回的实际改写词" text={detail.actual_prompt} />}
          {detail.record_type === "agent" && detail.output && <details><summary className="cursor-pointer text-sm font-medium text-fg">查看本次文本产出</summary><div className="mt-3"><PromptText label="产出存档" text={JSON.stringify(detail.output, null, 2)} /></div></details>}
          <details className="border-t border-border pt-4"><summary className="cursor-pointer text-sm text-fg-muted">技术详情与校验记录</summary><div className="mt-4 space-y-3 text-xs text-fg-muted">
            <p className="break-all">记录编号：{detail.id}</p><p>规则版本：{detail.rule_version || "历史版本未记录"}</p><p className="break-all">输入版本：{detail.basis_digest || "未记录"}</p>
            {detail.steps.map((step) => <div key={`${step.index}:${step.kind}`} className="rounded-[2px] bg-surface-2 p-3"><p className="flex items-baseline gap-x-4"><span className="tnum">尝试 {step.index + 1}</span><span>{step.kind === "validate" ? "校验" : "模型调用"}</span><span className="tnum ml-auto">{step.duration_ms} ms</span></p>{step.error && <p className="mt-2 whitespace-pre-wrap break-words text-danger">{step.error}</p>}</div>)}
          </div></details>
        </>}
      </div>
    </Dialog>
  </section>;
}
