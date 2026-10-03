"use client";

import * as React from "react";

import {
  BEAT_KINDS,
  BEAT_KIND_LABEL,
  type BeatDraft,
  type BeatField,
} from "@/lib/freeflow/screenplay-scope";
import { cn } from "@/lib/utils";

/**
 * 一场的节拍：阅读排版与逐条编辑两种形态。
 *
 * **条数不可变**：后端的字段级编辑只替换已存在的路径，不支持追加或删除数组
 * 元素（`content/patching.py` 第 1 条）。要多一条节拍只能返工重跑剧本 Agent，
 * 所以这里没有"加一条"按钮——那会是个假入口。
 *
 * 缺键的旧节拍（4 个键不齐）由 `planSceneDraft` 降级成整条替换，编辑时照常
 * 可填，只是保存时提交的是整条对象。`incomplete` 只用来告诉用户这件事。
 */

/** 阅读排版。与 `screenplay-view.tsx` 的纸面排版保持一致的读法。 */
export function BeatReading({ beats, hook }: { beats: BeatDraft[]; hook: string }) {
  if (beats.length === 0) {
    return <p className="text-xs text-fg-subtle">这一场没有节拍。只能返工整段剧本来补。</p>;
  }
  return (
    <div className="flex max-w-[68ch] flex-col gap-1 text-[13px] leading-7">
      {beats.map((beat, i) => (
        <p key={i} className="text-fg-muted">
          {beat.kind === "action" && `△${beat.text}`}
          {beat.kind === "sfx" && `【音效：${beat.text}】`}
          {beat.kind === "vo" && (
            <>
              <span className="text-fg">{beat.character_ref}（VO）</span>：{beat.text}
            </>
          )}
          {beat.kind === "dialogue" && (
            <>
              <span className="text-fg">
                {beat.character_ref}
                {beat.emotion && `（${beat.emotion}）`}
              </span>
              ：{beat.text}
            </>
          )}
          {!["action", "sfx", "vo", "dialogue"].includes(beat.kind) && beat.text}
        </p>
      ))}
      {hook && <p className="text-primary">【钩子】{hook}</p>}
    </div>
  );
}

export function BeatRows({
  beats,
  dirtyKeys,
  incomplete,
  disabled,
  characterSuggestions,
  onChange,
}: {
  beats: BeatDraft[];
  /** 改过还没保存的字段，`"{i}.{field}"` */
  dirtyKeys: ReadonlySet<string>;
  /** 4 个键不齐的节拍位置：保存时整条替换 */
  incomplete: ReadonlySet<number>;
  disabled: boolean;
  characterSuggestions: string[];
  onChange: (at: number, field: BeatField, value: string) => void;
}) {
  const listId = React.useId();

  if (beats.length === 0) {
    return (
      <p className="rounded-[2px] border border-dashed border-border px-3 py-6 text-center text-xs text-fg-subtle">
        这一场没有节拍。字段级编辑不能新增元素，只能返工整段剧本。
      </p>
    );
  }

  return (
    <>
      <datalist id={listId}>
        {characterSuggestions.map((name) => (
          <option key={name} value={name} />
        ))}
      </datalist>
      <ol className="flex flex-col gap-1.5">
        {beats.map((beat, at) => {
          const touched = (field: BeatField) => dirtyKeys.has(`${at}.${field}`);
          const rowDirty = (["kind", "character_ref", "emotion", "text"] as BeatField[]).some(touched);
          return (
            <li
              key={at}
              className={cn(
                "rounded-[2px] border px-2.5 py-2",
                rowDirty ? "border-rf-warning/50 bg-rf-warning-soft" : "border-border bg-surface",
              )}
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="tnum w-6 shrink-0 text-xs text-fg-subtle">{at + 1}</span>

                <label className="flex items-center gap-1">
                  <span className="sr-only">第 {at + 1} 条节拍的类型</span>
                  <select
                    value={beat.kind}
                    disabled={disabled}
                    onChange={(e) => onChange(at, "kind", e.target.value)}
                    className={cn(
                      "h-7 rounded-md border bg-bg px-1.5 text-xs text-fg focus:border-primary focus:outline-none disabled:opacity-60",
                      touched("kind") ? "border-rf-warning" : "border-border-strong",
                    )}
                  >
                    {BEAT_KINDS.map((kind) => (
                      <option key={kind} value={kind}>
                        {BEAT_KIND_LABEL[kind]}
                      </option>
                    ))}
                    {!BEAT_KINDS.includes(beat.kind as (typeof BEAT_KINDS)[number]) && (
                      // 旧产出里出现过词表外的值：列出来，否则 select 会静默把它改掉
                      <option value={beat.kind}>{beat.kind || "（空）"}</option>
                    )}
                  </select>
                </label>

                <label className="flex min-w-0 items-center gap-1">
                  <span className="sr-only">第 {at + 1} 条节拍的说话人</span>
                  <input
                    type="text"
                    value={beat.character_ref}
                    disabled={disabled}
                    list={listId}
                    maxLength={32}
                    placeholder="说话人"
                    onChange={(e) => onChange(at, "character_ref", e.target.value)}
                    className={cn(
                      "h-7 w-28 rounded-md border bg-bg px-2 text-xs text-fg placeholder:text-fg-subtle focus:border-primary focus:outline-none disabled:opacity-60",
                      touched("character_ref") ? "border-rf-warning" : "border-border-strong",
                    )}
                  />
                </label>

                <label className="flex min-w-0 items-center gap-1">
                  <span className="sr-only">第 {at + 1} 条节拍的情绪</span>
                  <input
                    type="text"
                    value={beat.emotion}
                    disabled={disabled}
                    maxLength={40}
                    placeholder="情绪"
                    onChange={(e) => onChange(at, "emotion", e.target.value)}
                    className={cn(
                      "h-7 w-24 rounded-md border bg-bg px-2 text-xs text-fg placeholder:text-fg-subtle focus:border-primary focus:outline-none disabled:opacity-60",
                      touched("emotion") ? "border-rf-warning" : "border-border-strong",
                    )}
                  />
                </label>

                {incomplete.has(at) && (
                  <span
                    title="这条节拍是旧格式，缺字段。保存时整条一起写回去。"
                    className="ml-auto text-[11px] text-fg-subtle"
                  >
                    旧格式 · 整条保存
                  </span>
                )}
              </div>

              <label className="mt-1.5 block">
                <span className="sr-only">第 {at + 1} 条节拍的内容</span>
                <textarea
                  rows={2}
                  value={beat.text}
                  disabled={disabled}
                  maxLength={300}
                  placeholder="动作 / 台词 / 音效内容"
                  onChange={(e) => onChange(at, "text", e.target.value)}
                  className={cn(
                    "w-full resize-y rounded-md border bg-bg px-2 py-1.5 text-[13px] leading-6 text-fg placeholder:text-fg-subtle focus:border-primary focus:outline-none disabled:opacity-60",
                    touched("text") ? "border-rf-warning" : "border-border-strong",
                  )}
                />
              </label>
            </li>
          );
        })}
      </ol>
    </>
  );
}
