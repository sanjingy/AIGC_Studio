"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useDropzone, type FileRejection } from "react-dropzone";
import { AlertTriangle, Check, FileUp, Loader2, Play, RotateCw, Save, Undo2 } from "lucide-react";

import { ConfirmDialog } from "@/components/freeflow/project/feedback";
import { Button } from "@/components/ui/button";
import { auth } from "@/lib/api";
import {
  SOURCE_FILE_ACCEPT,
  SOURCE_FILE_MAX_BYTES,
  SOURCE_MAX,
  charCount,
  clearDraft,
  decodeStoryFile,
  draftKey,
  draftToRestore,
  fileRejectionText,
  readDraft,
  sourceDirty,
  sourceProblem,
  writeDraft,
} from "@/lib/freeflow/story-source";
import { useLeaveGuard } from "@/lib/freeflow/use-leave-guard";
import type { ProjectState } from "@/lib/freeflow/use-project-state";
import { cn } from "@/lib/utils";

import { ActionError } from "./production-actions";

/** localStorage 在隐私模式、预览里可能直接抛错；拿不到就当没有草稿保险 */
function draftStore(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/**
 * 故事原文：查看、粘贴或导入、免费保存，然后才开始付费生产。
 *
 * 三条路分开，按钮上写清楚花不花钱：
 *
 * - **保存原文**：`PUT /projects/{id}/source`，不调模型、不扣 Credits；
 * - **导入文件**：只在浏览器里把 .txt / .md 读成文字放进输入框（react-dropzone，
 *   MIT，见 orca/tasks/USER_FLOW_REPAIR/reuse.md），**不上传文件**，仍要点保存；
 * - **开始生产**：确认后 `advance`，会调用模型、扣 Credits。原文没保存、
 *   或有未保存的修改时不给点——否则跑的是哪一版原文说不清。
 *
 * 未保存的修改同时落一份本地草稿（按账号 + 项目隔离），刷新或误关后回来能恢复。
 * 已有阶段产出后原文只读：后端会 409，界面不摆一个点了必失败的输入框。
 */
export function StorySourcePanel({
  projectId,
  state,
  onDirtyChange,
}: {
  projectId: string;
  state: ProjectState;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const textId = useId();
  const saved = state.savedSource;
  const [draft, setDraft] = useState(saved);
  const [userId, setUserId] = useState<string | null>(null);
  const [restoredAt, setRestoredAt] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 原文已存上、但保存后重拉项目状态失败：页面其余部分可能是旧的，要明说并给重试
  const [refreshFailed, setRefreshFailed] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [pendingFile, setPendingFile] = useState<{ name: string; text: string; note: string } | null>(null);
  const [confirmStart, setConfirmStart] = useState(false);
  const restoredOnce = useRef(false);

  const key = userId ? draftKey(userId, projectId) : null;
  const editable = state.sourceEditable && state.busy !== "advance";
  const saving = state.busy === "source";
  const dirty = editable && sourceDirty(draft, saved);
  const problem = sourceProblem(draft);
  const chars = charCount(draft.trim());

  // 草稿要分账号存，先问一次"我是谁"。拿不到就不读写草稿，编辑照常
  useEffect(() => {
    let alive = true;
    auth
      .me()
      .then((u) => alive && setUserId(u.id))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  // 恢复只做一次：有草稿且与服务端那份不同才恢复，并明说恢复了什么
  useEffect(() => {
    if (!key || restoredOnce.current || !editable) return;
    restoredOnce.current = true;
    const draftFound = draftToRestore(readDraft(draftStore(), key), saved);
    if (draftFound) {
      setDraft(draftFound.text);
      setRestoredAt(draftFound.savedAt);
    }
  }, [key, saved, editable]);

  // 服务端那份变了（保存成功、别处改了）而这里没有修改：跟上它
  const lastSaved = useRef(saved);
  useEffect(() => {
    if (lastSaved.current === saved) return;
    setDraft((current) => (sourceDirty(current, lastSaved.current) ? current : saved));
    lastSaved.current = saved;
  }, [saved]);

  // 未保存的修改写进本地草稿；与已保存一致就清掉，不留过期副本
  useEffect(() => {
    if (!key || !editable) return;
    const timer = window.setTimeout(() => {
      if (sourceDirty(draft, saved)) writeDraft(draftStore(), key, draft);
      else clearDraft(draftStore(), key);
    }, 400);
    return () => window.clearTimeout(timer);
  }, [draft, saved, key, editable]);

  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);
  useLeaveGuard(dirty);

  const applyText = useCallback((text: string, note: string) => {
    setDraft(text);
    setRestoredAt(null);
    setError(null);
    setNotice(note);
  }, []);

  const onDrop = useCallback(
    async (accepted: File[], rejected: FileRejection[]) => {
      setNotice(null);
      if (rejected.length > 0) {
        const first = rejected[0]!.errors[0];
        setError(fileRejectionText(first?.code ?? "", first?.message ?? ""));
        return;
      }
      const file = accepted[0];
      if (!file) return;
      let bytes: Uint8Array;
      try {
        bytes = new Uint8Array(await file.arrayBuffer());
      } catch {
        setError(`读不了「${file.name}」，确认文件还在、没有被别的程序占用`);
        return;
      }
      const decoded = decodeStoryFile(bytes);
      if (!decoded.ok) {
        setError(`「${file.name}」：${decoded.error}`);
        return;
      }
      const encoding = decoded.encoding === "gb18030" ? "GBK" : decoded.encoding.toUpperCase();
      const note = `已从「${file.name}」读入 ${charCount(decoded.text.trim()).toLocaleString("zh-CN")} 字（${encoding}），还没保存。`;
      // 输入框里已经有别的内容：先问一句，不悄悄盖掉
      if (draft.trim() && draft.trim() !== decoded.text.trim()) {
        setPendingFile({ name: file.name, text: decoded.text, note });
        return;
      }
      applyText(decoded.text, note);
    },
    [draft, applyText],
  );

  const { getRootProps, getInputProps, isDragActive, open } = useDropzone({
    accept: SOURCE_FILE_ACCEPT,
    maxSize: SOURCE_FILE_MAX_BYTES,
    maxFiles: 1,
    multiple: false,
    // 点击输入框是要打字，不是选文件；选文件只走那颗按钮
    noClick: true,
    noKeyboard: true,
    disabled: !editable || saving,
    onDrop: (accepted, rejected) => void onDrop(accepted, rejected),
  });

  async function save() {
    if (problem || !dirty) return;
    setError(null);
    setNotice(null);
    setRefreshFailed(false);
    const result = await state.saveSource(draft.trim());
    if (result.ok) {
      if (key) clearDraft(draftStore(), key);
      setRestoredAt(null);
      setNotice("原文已保存到项目，刷新或换设备都在。没有调用模型，也没有扣 Credits。");
      setRefreshFailed(!result.refreshed);
    } else {
      setError(`没有保存：${result.message}。你的修改还在输入框里。`);
    }
  }

  async function retryRefresh() {
    setRefreshing(true);
    try {
      await state.reload({ requireState: true });
      setRefreshFailed(false);
    } catch {
      // 仍然失败：提示留着，可以再点
    } finally {
      setRefreshing(false);
    }
  }

  function discard() {
    setDraft(saved);
    setRestoredAt(null);
    setError(null);
    setNotice(null);
    if (key) clearDraft(draftStore(), key);
  }

  const hasSaved = saved.trim() !== "";
  const startBlocked = !hasSaved
    ? "先保存原文，才能开始生产"
    : dirty
      ? "有未保存的修改，先保存或放弃，再开始生产"
      : state.pendingGate
        ? "有审核门等待处理"
        : state.busy !== null
          ? "正在处理上一个操作"
          : null;
  const working = state.busy === "advance";

  if (!editable && !working) {
    return (
      <section aria-labelledby={`${textId}-h`} className="rounded-[2px] border border-border bg-surface">
        <SourceHeader id={`${textId}-h`} chars={charCount(saved.trim())} status="已保存，只读" />
        {hasSaved ? (
          <p className="max-h-80 overflow-y-auto px-4 py-3 text-sm leading-7 whitespace-pre-wrap text-fg-muted">{saved}</p>
        ) : (
          <p className="px-4 py-3 text-sm text-fg-subtle">这个项目没有保存原文。</p>
        )}
        <p className="border-t border-border px-4 py-2 text-xs leading-5 text-fg-subtle">
          已经有生产产出或待确认的审核门，原文不能直接替换——那样产出会和原文对不上。要改内容，请在对应阶段用返工。
        </p>
      </section>
    );
  }

  return (
    <section aria-labelledby={`${textId}-h`} className="rounded-[2px] border border-border-strong bg-surface">
      <SourceHeader
        id={`${textId}-h`}
        chars={chars}
        over={chars > SOURCE_MAX}
        status={dirty ? "有未保存的修改" : hasSaved ? "已保存到项目" : "还没有原文"}
        tone={dirty ? "warn" : hasSaved ? "ok" : "muted"}
      />

      <div className="flex flex-col gap-3 p-4">
        {restoredAt && (
          <p role="status" className="flex flex-wrap items-center gap-2 rounded-md bg-running-soft px-3 py-2 text-xs text-running">
            <span className="min-w-0 flex-1">
              已恢复你 {new Date(restoredAt).toLocaleString("zh-CN", { hour12: false })} 没保存的草稿。确认无误后点「保存原文」。
            </span>
            <Button size="sm" variant="ghost" onClick={discard}>
              丢弃草稿
            </Button>
          </p>
        )}

        <div
          {...getRootProps({
            className: cn(
              "relative flex flex-col gap-1.5 rounded-md",
              isDragActive && "outline-2 outline-dashed outline-offset-2 outline-primary",
            ),
          })}
        >
          <input {...getInputProps({ "aria-label": "选择 .txt 或 .md 原文文件" })} />
          <label htmlFor={textId} className="text-sm font-medium text-fg">
            小说原文或创意
          </label>
          <textarea
            id={textId}
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              setRestoredAt(null);
              setNotice(null);
            }}
            disabled={saving || working}
            rows={12}
            aria-describedby={`${textId}-hint`}
            aria-invalid={chars > SOURCE_MAX || undefined}
            placeholder="把小说原文粘进来，或写一句创意；也可以把 .txt / .md 文件拖到这里。"
            className="min-h-48 resize-y rounded-md border border-border-strong bg-bg px-3 py-2 text-sm leading-6 text-fg placeholder:text-fg-subtle focus:border-primary disabled:opacity-70"
          />
          {isDragActive && (
            <div className="pointer-events-none absolute inset-0 grid place-items-center rounded-md bg-surface/85 text-sm font-medium text-primary">
              松开即可读入文件（不会上传）
            </div>
          )}
          <p id={`${textId}-hint`} className="text-xs leading-5 text-fg-subtle">
            上限 {SOURCE_MAX.toLocaleString("zh-CN")} 字。支持 .txt、.md（UTF-8 或 GBK 编码）；Word、PDF 请先另存为 .txt。
            文件只在本页读成文字，不会上传。
          </p>
        </div>

        {error && (
          <p role="alert" className="flex items-start gap-1.5 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
            <AlertTriangle aria-hidden className="mt-0.5 size-4 shrink-0" />
            {error}
          </p>
        )}
        {!error && dirty && problem && draft.trim() !== "" && (
          <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
            {problem}
          </p>
        )}
        {notice && (
          <p role="status" className="flex items-start gap-1.5 rounded-md bg-surface-2 px-3 py-2 text-sm text-fg-muted">
            <Check aria-hidden className="mt-0.5 size-4 shrink-0 text-success" />
            {notice}
          </p>
        )}

        {refreshFailed && (
          <div
            role="alert"
            className="flex flex-wrap items-center gap-2 rounded-md bg-rf-warning-soft px-3 py-2 text-sm text-rf-warning"
          >
            <AlertTriangle aria-hidden className="size-4 shrink-0" />
            <span className="min-w-0 flex-1">原文已经保存，但刷新项目状态失败，页面其他部分可能还是旧的。</span>
            <Button size="sm" variant="secondary" disabled={refreshing} onClick={() => void retryRefresh()}>
              {refreshing ? <Loader2 aria-hidden className="size-4 animate-spin" /> : <RotateCw aria-hidden className="size-4" />}
              重新加载
            </Button>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={open} disabled={saving || working}>
            <FileUp aria-hidden className="size-4" />
            导入 .txt / .md 文件
          </Button>
          <Button
            variant={dirty && !problem ? "primary" : "secondary"}
            disabled={!dirty || problem !== null || saving || working}
            onClick={() => void save()}
          >
            {saving ? <Loader2 aria-hidden className="size-4 animate-spin" /> : <Save aria-hidden className="size-4" />}
            保存原文
          </Button>
          {dirty && hasSaved && (
            <Button variant="ghost" disabled={saving || working} onClick={discard}>
              <Undo2 aria-hidden className="size-4" />
              放弃修改
            </Button>
          )}
          <span className="text-xs text-fg-subtle">保存免费：不调用模型、不扣 Credits。</span>
        </div>
      </div>

      <div className="flex flex-col gap-2 border-t border-border px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0 text-xs leading-5 text-fg-subtle">
          <p className="flex items-start gap-1.5">
            <AlertTriangle aria-hidden className="mt-0.5 size-3.5 shrink-0" />
            下一步「开始生产」会调用模型、扣 Credits：依次跑路线判断和情节目录，停在「开拍前确认」等你定画风。
          </p>
          {startBlocked && !working && <p className="mt-0.5 text-fg-muted">{startBlocked}。</p>}
        </div>
        <Button
          variant="primary"
          disabled={startBlocked !== null || working}
          onClick={() => setConfirmStart(true)}
          className="shrink-0"
        >
          {working ? <Loader2 aria-hidden className="size-4 animate-spin" /> : <Play aria-hidden className="size-4" />}
          {working ? "生成中…" : "开始生产"}
        </Button>
      </div>
      {state.actionError && state.busy === null && (
        <div className="px-4 pb-3">
          <ActionError state={state} />
        </div>
      )}

      <ConfirmDialog
        open={pendingFile !== null}
        title="用文件内容替换输入框？"
        description={`输入框里已经有 ${chars.toLocaleString("zh-CN")} 字。替换后它们会被「${pendingFile?.name ?? ""}」的内容覆盖（已保存到项目的那一版不受影响，点「放弃修改」可以退回）。`}
        confirmLabel="替换"
        onCancel={() => setPendingFile(null)}
        onConfirm={() => {
          if (pendingFile) applyText(pendingFile.text, pendingFile.note);
          setPendingFile(null);
        }}
      />
      <ConfirmDialog
        open={confirmStart}
        title="开始生产？"
        description={`会用已保存的 ${charCount(saved.trim()).toLocaleString("zh-CN")} 字原文调用模型、扣 Credits：跑路线判断和情节目录，然后停在「开拍前确认」。之后每一步都等你确认。`}
        confirmLabel="确认开始生产"
        onCancel={() => setConfirmStart(false)}
        onConfirm={() => {
          setConfirmStart(false);
          void state.advance("");
        }}
      />
    </section>
  );
}

function SourceHeader({
  id,
  chars,
  over = false,
  status,
  tone = "muted",
}: {
  id: string;
  chars: number;
  over?: boolean;
  status: string;
  tone?: "ok" | "warn" | "muted";
}) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-b border-border px-4 py-2.5">
      <h2 id={id} className="text-sm font-semibold text-fg">
        故事原文
      </h2>
      <span className="flex items-baseline gap-3 text-xs">
        <span
          className={cn(
            tone === "ok" && "text-success",
            tone === "warn" && "text-running",
            tone === "muted" && "text-fg-subtle",
          )}
        >
          {status}
        </span>
        <span className={cn("tnum text-fg-subtle", over && "text-danger")}>
          {chars.toLocaleString("zh-CN")} / {SOURCE_MAX.toLocaleString("zh-CN")} 字
        </span>
      </span>
    </div>
  );
}
