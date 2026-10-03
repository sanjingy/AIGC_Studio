"use client";

import * as React from "react";
import { Loader2, Save, Undo2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { ContentEdit } from "@/lib/freeflow/use-content-edit";
import {
  dirtyFields,
  parseList,
  planPatches,
  rebaseDraft,
  type Draft,
  type FieldSpec,
} from "@/lib/freeflow/profile-scope";
import { useLeaveGuard } from "@/lib/freeflow/use-leave-guard";
import { cn } from "@/lib/utils";

/**
 * 一个角色 / 场景档案的逐字段编辑（ADR-029）。
 *
 * 字段、只读原因、补丁路径全部由 `profile-scope.ts` 算好传进来，这里只管
 * 草稿与提交：**一次保存 = 一批**，只带改过的字段；失败时草稿原样留着。
 * 父组件以对象为 key 重建它，所以换对象不会带着上一个对象的草稿。
 */
export function ProfileEditor({
  fields,
  edit,
  onDirtyChange,
  saveLabel,
  readOnly,
}: {
  fields: FieldSpec[];
  edit: ContentEdit;
  onDirtyChange: (dirty: boolean) => void;
  saveLabel: string;
  /** 表单上方的只读信息（ref、推断字段、旧体型描述……） */
  readOnly?: React.ReactNode;
}) {
  const base = React.useMemo(() => {
    const out: Draft = {};
    for (const f of fields) out[f.id] = f.value;
    return out;
  }, [fields]);

  const [draft, setDraft] = React.useState<Draft>(base);
  const [reason, setReason] = React.useState("");

  /**
   * 库里的值变了（保存成功、撤销、返工）时草稿逐字段跟上新值，规则见
   * `rebaseDraft`。按内容判断，不按引用——SSE 和任务轮询会让 `fields`
   * 换新对象但值不变。
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
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 按内容键触发，见上
  }, [baseKey]);

  const dirty = React.useMemo(() => new Set(dirtyFields(fields, draft).map((f) => f.id)), [fields, draft]);
  const hasChanges = dirty.size > 0;
  useLeaveGuard(hasChanges);
  React.useEffect(() => onDirtyChange(hasChanges), [hasChanges, onDirtyChange]);
  React.useEffect(() => () => onDirtyChange(false), [onDirtyChange]);

  const discard = () => {
    setDraft(base);
    setReason("");
    edit.clearError();
  };

  const save = async () => {
    const patches = planPatches(fields, draft);
    if (patches.length === 0) return;
    justSaved.current = true;
    const result = await edit.save(patches, reason);
    if (result) setReason("");
    else justSaved.current = false;
  };

  const groups: [string, FieldSpec[]][] = [];
  for (const f of fields) {
    const last = groups[groups.length - 1];
    if (last && last[0] === f.group) last[1].push(f);
    else groups.push([f.group, [f]]);
  }

  return (
    <section aria-label="档案" className="flex min-w-0 flex-col">
      {readOnly && <div className="mb-3 flex flex-wrap gap-1.5">{readOnly}</div>}

      <div className="flex flex-col gap-4">
        {groups.map(([group, list]) => (
          <fieldset key={group} className="min-w-0 rounded-[2px] border border-border bg-surface px-4 pt-2 pb-3">
            <legend className="px-1 text-xs font-semibold text-fg">{group}</legend>
            <div className="grid gap-3 sm:grid-cols-2">
              {list.map((f) => (
                <FieldInput
                  key={f.id}
                  field={f}
                  value={draft[f.id] ?? f.value}
                  dirty={dirty.has(f.id)}
                  disabled={edit.saving}
                  onChange={(value) => setDraft((prev) => ({ ...prev, [f.id]: value }))}
                />
              ))}
            </div>
          </fieldset>
        ))}
      </div>

      <div
        className={cn(
          // 贴在主区底部：长表单滚下去以后保存仍然够得着（390 屏尤其如此）
          "sticky bottom-0 z-10 mt-4 flex flex-wrap items-center gap-2 border-t px-1 py-3",
          hasChanges ? "border-rf-warning/50 bg-rf-warning-soft" : "border-border bg-bg",
        )}
      >
        <span className={cn("text-xs", hasChanges ? "font-medium text-fg" : "text-fg-subtle")}>
          {hasChanges ? `${dirty.size} 个字段改过还没保存` : "改动保存后刷新仍在，可在改动记录里撤销"}
        </span>
        <input
          type="text"
          value={reason}
          disabled={edit.saving}
          placeholder="改动说明（可选）"
          aria-label="改动说明"
          onChange={(e) => setReason(e.target.value)}
          className="ml-auto h-7 w-full max-w-[14rem] rounded-md border border-border-strong bg-bg px-2 text-xs text-fg focus:border-primary focus:outline-none disabled:opacity-60 max-sm:max-w-none"
        />
        <Button size="sm" disabled={!hasChanges || edit.saving} onClick={discard}>
          <Undo2 aria-hidden className="size-3.5" />
          放弃改动
        </Button>
        <Button size="sm" variant="primary" disabled={!hasChanges || edit.saving} onClick={() => void save()}>
          {edit.saving ? <Loader2 aria-hidden className="size-3.5 animate-spin" /> : <Save aria-hidden className="size-3.5" />}
          {saveLabel}
        </Button>
        {edit.error && (
          <p role="alert" className="w-full rounded-md bg-danger-soft px-2.5 py-1.5 text-xs leading-5 text-danger">
            {edit.error}
          </p>
        )}
      </div>
    </section>
  );
}

function FieldInput({
  field,
  value,
  dirty,
  disabled,
  onChange,
}: {
  field: FieldSpec;
  value: string | string[];
  dirty: boolean;
  disabled: boolean;
  onChange: (value: string | string[]) => void;
}) {
  const id = React.useId();
  const hintId = `${id}-hint`;
  const locked = field.blocked !== null;
  const wide = field.control === "multiline" || field.control === "list";
  const cls = cn(
    "w-full rounded-md border bg-bg px-2 py-1.5 text-[13px] leading-6 text-fg placeholder:text-fg-subtle focus:border-primary focus:outline-none disabled:opacity-60",
    dirty ? "border-rf-warning" : "border-border-strong",
  );
  const note = field.blocked ?? field.hint;

  /**
   * 列表字段的文本框里保留用户正在敲的原样（含空行），只在比较与提交时解析。
   * 直接把解析结果回写进输入框，用户按回车的那一刻新行就被吃掉了。
   */
  const [listText, setListText] = React.useState(() => (Array.isArray(value) ? value.join("\n") : ""));
  const listKey = Array.isArray(value) ? value.join("\n") : "";
  React.useEffect(() => {
    if (field.control !== "list") return;
    setListText((prev) => (parseList(prev).join("\n") === listKey ? prev : listKey));
  }, [listKey, field.control]);

  let input: React.ReactNode;
  if (locked) {
    input = (
      <p className="min-h-8 rounded-md border border-dashed border-border px-2 py-1.5 text-[13px] leading-6 text-fg-muted">
        {(Array.isArray(field.value) ? field.value.join("、") : field.value) || <span className="text-fg-subtle italic">（空）</span>}
      </p>
    );
  } else if (field.control === "select") {
    input = (
      <select id={id} value={value as string} disabled={disabled} aria-describedby={note ? hintId : undefined} onChange={(e) => onChange(e.target.value)} className={cn(cls, "h-8 py-0")}>
        {(field.options ?? []).map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    );
  } else if (field.control === "list") {
    input = (
      <textarea
        id={id}
        rows={3}
        value={listText}
        disabled={disabled}
        aria-describedby={note ? hintId : undefined}
        onChange={(e) => {
          setListText(e.target.value);
          onChange(parseList(e.target.value));
        }}
        className={cn(cls, "resize-y")}
      />
    );
  } else if (field.control === "multiline") {
    input = (
      <textarea id={id} rows={3} value={value as string} disabled={disabled} maxLength={field.maxLength} aria-describedby={note ? hintId : undefined} onChange={(e) => onChange(e.target.value)} className={cn(cls, "resize-y")} />
    );
  } else {
    input = (
      <input id={id} type="text" value={value as string} disabled={disabled} maxLength={field.maxLength} aria-describedby={note ? hintId : undefined} onChange={(e) => onChange(e.target.value)} className={cls} />
    );
  }

  return (
    <div className={cn("flex min-w-0 flex-col gap-1", wide && "sm:col-span-2")}>
      {/* 只读字段的值是一段文字不是输入框，label 关联不上它，用普通文字 */}
      {locked ? (
        <span className="text-xs font-medium text-fg">
          {field.label}
          <span className="ml-1.5 font-normal text-fg-subtle">只读</span>
        </span>
      ) : (
        <label htmlFor={id} className="text-xs font-medium text-fg">
          {field.label}
          {dirty && <span className="ml-1.5 text-rf-warning">已改</span>}
        </label>
      )}
      {input}
      {note && (
        <p id={hintId} className="text-[11px] leading-4 text-fg-subtle">
          {note}
        </p>
      )}
    </div>
  );
}
