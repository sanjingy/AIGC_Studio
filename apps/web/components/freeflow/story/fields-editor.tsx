"use client";

import * as React from "react";
import { Loader2, Pencil, Save, Undo2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { PatchOp } from "@/lib/api";
import { rebaseDraft } from "@/lib/freeflow/draft-rebase";
import type { ContentEdit } from "@/lib/freeflow/use-content-edit";
import { useLeaveGuard } from "@/lib/freeflow/use-leave-guard";
import { cn } from "@/lib/utils";

/**
 * 几个文本字段的阅读 / 编辑卡。剧本标题与梗概、集标题都用它。
 *
 * 与 `SceneEditor` 同样的三条约定：一次保存一批、未保存显眼且拦得住、
 * 保存后用响应里的整块产出更新（在 `useContentEdit` 里做，不 refetch）。
 *
 * `present=false` 的字段**不可编辑**：后端的字段级编辑只替换已存在的路径，
 * 提交一个不存在的键必然被拒（`content/patching.py` 第 1 条）。
 */

export type EditableField = {
  /** JSON Pointer，相对整块产出。**走数组位置**，不是展示编号 */
  path: string;
  label: string;
  /** 库里现在的值 */
  value: string;
  /** 这个键在产出里存在 */
  present: boolean;
  multiline?: boolean;
  maxLength: number;
  placeholder?: string;
};

export function FieldsEditor({
  fields,
  edit,
  onDirtyChange,
  heading,
  hint,
  saveLabel,
}: {
  fields: EditableField[];
  edit: ContentEdit;
  onDirtyChange: (dirty: boolean) => void;
  heading: string;
  hint?: string;
  saveLabel: string;
}) {
  const base = React.useMemo(() => {
    const out: Record<string, string> = {};
    for (const field of fields) out[field.path] = field.value;
    return out;
  }, [fields]);

  const [draft, setDraft] = React.useState<Record<string, string>>(base);
  const [open, setOpen] = React.useState(false);
  const [reason, setReason] = React.useState("");

  /**
   * 草稿什么时候跟着库里的值重置。
   *
   * 按**内容**判断，不按引用：父组件因为 dirty 回报、SSE、任务轮询重渲染时，
   * `fields` 可能是新对象但值没变，按引用重置会把用户正在输入的内容冲掉。
   * 内容真的变了（换了一集、保存成功、撤销、别处写了库）时逐字段跟：
   * 没动过的字段换成新值，动过的保留草稿；自己刚保存的结果整份换新值
   * （`rebaseDraft`）。
   */
  const baseKey = JSON.stringify(base);
  const justSaved = React.useRef(false);
  const prevBase = React.useRef(base);
  React.useEffect(() => {
    const takeAll = justSaved.current;
    const from = prevBase.current;
    setDraft((current) => rebaseDraft(from, base, current, takeAll));
    prevBase.current = base;
    justSaved.current = false;
  }, [baseKey]);

  const dirty = React.useMemo(() => {
    const out = new Set<string>();
    for (const field of fields) {
      if (!field.present) continue;
      if ((draft[field.path] ?? "") !== field.value) out.add(field.path);
    }
    return out;
  }, [draft, fields]);

  const hasChanges = dirty.size > 0;
  useLeaveGuard(hasChanges);
  React.useEffect(() => onDirtyChange(hasChanges), [hasChanges, onDirtyChange]);
  // 卸载时撤掉自己报上去的 dirty，否则父级会一直以为还有草稿
  React.useEffect(() => () => onDirtyChange(false), [onDirtyChange]);
  React.useEffect(() => {
    if (hasChanges) setOpen(true);
  }, [hasChanges]);

  const discard = () => {
    setDraft(base);
    setReason("");
    edit.clearError();
  };

  const save = async () => {
    const patches: PatchOp[] = [...dirty].map((path) => ({ path, value: draft[path] ?? "" }));
    if (patches.length === 0) return;
    justSaved.current = true;
    const result = await edit.save(patches, reason);
    if (result) setReason("");
    // 失败时草稿原样留着；标志复位，别让下一次外部变化误清草稿
    else justSaved.current = false;
  };

  return (
    <section
      className={cn(
        "rounded-[2px] border bg-surface",
        hasChanges ? "border-rf-warning/50" : "border-border",
      )}
      aria-label={heading}
    >
      <div className="flex flex-wrap items-baseline gap-2 border-b border-border px-4 py-2.5">
        <h3 className="text-sm font-semibold text-fg">{heading}</h3>
        {hint && <span className="text-xs text-fg-subtle">{hint}</span>}
        <Button
          size="sm"
          variant="ghost"
          className="ml-auto"
          aria-expanded={open}
          disabled={hasChanges}
          title={hasChanges ? "有未保存的改动，先保存或放弃" : undefined}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? <X aria-hidden className="size-3.5" /> : <Pencil aria-hidden className="size-3.5" />}
          {open ? "收起" : "编辑"}
        </Button>
      </div>

      <div className="flex flex-col gap-3 px-4 py-3">
        {fields.map((field) =>
          open && field.present ? (
            <Field
              key={field.path}
              field={field}
              value={draft[field.path] ?? ""}
              dirty={dirty.has(field.path)}
              disabled={edit.saving}
              onChange={(value) => setDraft((prev) => ({ ...prev, [field.path]: value }))}
            />
          ) : (
            <div key={field.path} className="flex flex-col gap-1">
              <span className="text-xs font-medium text-fg">{field.label}</span>
              <p className="max-w-[68ch] text-[13px] leading-6 text-fg-muted">
                {field.value || <span className="text-fg-subtle italic">（空）</span>}
              </p>
              {open && !field.present && (
                <p className="text-xs text-fg-subtle">
                  这份旧产出没有这个字段，不能单独补——只能返工整段剧本。
                </p>
              )}
            </div>
          ),
        )}

        {open && (
          <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
            <span className={cn("text-xs", hasChanges ? "font-medium text-fg" : "text-fg-subtle")}>
              {hasChanges
                ? `${dirty.size} 个字段改过还没保存`
                : "改动直接写回后端；不花 Credits，也不会重跑 Agent"}
            </span>
            <input
              type="text"
              value={reason}
              disabled={edit.saving}
              placeholder="改动说明（可选）"
              aria-label="改动说明"
              onChange={(e) => setReason(e.target.value)}
              className="ml-auto h-7 w-full max-w-[14rem] rounded-md border border-border-strong bg-bg px-2 text-xs text-fg focus:border-primary focus:outline-none disabled:opacity-60"
            />
            <Button size="sm" disabled={!hasChanges || edit.saving} onClick={discard}>
              <Undo2 aria-hidden className="size-3.5" />
              放弃改动
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={!hasChanges || edit.saving}
              onClick={() => void save()}
            >
              {edit.saving ? (
                <Loader2 aria-hidden className="size-3.5 animate-spin" />
              ) : (
                <Save aria-hidden className="size-3.5" />
              )}
              {saveLabel}
            </Button>
            {edit.error && (
              <p
                role="alert"
                className="w-full rounded-md bg-danger-soft px-2.5 py-1.5 text-xs leading-5 text-danger"
              >
                {edit.error}
              </p>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

function Field({
  field,
  value,
  dirty,
  disabled,
  onChange,
}: {
  field: EditableField;
  value: string;
  dirty: boolean;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const id = React.useId();
  const className = cn(
    "w-full rounded-md border bg-bg px-2 py-1.5 text-[13px] leading-6 text-fg placeholder:text-fg-subtle focus:border-primary focus:outline-none disabled:opacity-60",
    dirty ? "border-rf-warning" : "border-border-strong",
  );
  return (
    // label 只包字段名：包住 textarea 会把正文算进可访问名称
    <div className="flex flex-col gap-1">
      <label className="text-xs font-medium text-fg" htmlFor={id}>
        {field.label}
        {dirty && <span className="ml-1.5 text-rf-warning">已改</span>}
      </label>
      {field.multiline ? (
        <textarea
          id={id}
          rows={4}
          value={value}
          disabled={disabled}
          maxLength={field.maxLength}
          placeholder={field.placeholder}
          onChange={(e) => onChange(e.target.value)}
          className={cn(className, "resize-y")}
        />
      ) : (
        <input
          id={id}
          type="text"
          value={value}
          disabled={disabled}
          maxLength={field.maxLength}
          placeholder={field.placeholder}
          onChange={(e) => onChange(e.target.value)}
          className={className}
        />
      )}
    </div>
  );
}
