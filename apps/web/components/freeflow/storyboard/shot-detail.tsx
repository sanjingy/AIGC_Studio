// 视觉来自 ReelFlow 原型，数据由页面注入
"use client";

import * as React from "react";
import { Camera, MapPin, MessageSquare, Plus, RefreshCw, UserRound, X } from "lucide-react";

import { cn } from "@/lib/utils";

import type { ShotCardData } from "./shot-card";
import { ShotImage } from "./shot-image";

/**
 * 单镜详情**兼编辑面板**（ADR-029 / FR-WEB-004）。
 *
 * 这个文件只管版式和控件，**不持有草稿、不发请求**——那些在
 * `shot-editor.tsx` 里。拆开是因为"改了没保存怎么办"是一套状态机，
 * 而它和这里的两栏布局各自都会变，混在一个文件里改一处要读另一处。
 *
 * 可编辑的字段以后端 `agents/schemas.py` 的 `StoryboardShot` 为准，
 * **schema 里没有的字段一个都不加**（历史上加过"情绪/氛围"这种纯演示
 * 字段，已经删过一次）。合法性一律由后端按同一个 schema 判，这里的
 * 建议列表和字数提示都只是提示，不是第二套校验。
 */

/** 可编辑的九个字段。`index` / `node_index` 不在内，理由见下面 `SpecItem`。 */
export type ShotFieldKey =
  | "scene_ref"
  | "character_refs"
  | "shot_size"
  | "angle"
  | "camera_move"
  | "content"
  | "speaker_ref"
  | "dialogue"
  | "sfx";

export type ShotDraft = {
  scene_ref: string;
  character_refs: string[];
  shot_size: string;
  angle: string;
  camera_move: string;
  content: string;
  speaker_ref: string;
  dialogue: string;
  sfx: string;
};

/**
 * 字段名 → 界面上的叫法。变更历史要把 `/shots/0/shot_size` 说成
 * 「S01 · 景别」，两处各写一份必然对不上，所以只有这一份。
 * 键是后端 `StoryboardShot` 的字段名，含两个只读字段。
 */
export const SHOT_FIELD_LABEL: Record<string, string> = {
  index: "镜号",
  node_index: "节点",
  scene_ref: "场景",
  character_refs: "出场人物",
  shot_size: "景别",
  angle: "角度",
  camera_move: "运镜",
  content: "画面内容",
  speaker_ref: "说话人",
  dialogue: "台词",
  sfx: "音效",
};

/**
 * 景别的建议值，抄自后端 `STORYBOARD_SHOT_SIZES`。
 *
 * 用 `datalist` 而不是 `select`：**它是建议，不是白名单**。做成下拉框就
 * 等于在前端复制了一份枚举，后端加一个值前端就选不出来；而 datalist
 * 允许自由输入，后端加值当天就能用，删值也会被后端顶回来并带上原文
 * （pydantic 的 Literal 报错本身就把可选值列全了）。
 */
const SHOT_SIZE_SUGGESTIONS = [
  "极近特写",
  "特写",
  "近景胸像",
  "中景腰部",
  "中全景",
  "全景",
  "大全景",
  "航拍俯瞰",
];

/**
 * 字数上限，抄自 `StoryboardShot` 的 `max_length`。
 *
 * **不做 `maxLength` 硬截断，只显示计数**：硬截断会在后端放宽上限的那天
 * 拦住合法输入，而用户完全看不出是被前端拦的。超了就让它发出去，
 * 后端会带着字段名和原因打回来。
 */
const LIMIT: Partial<Record<ShotFieldKey, number>> = {
  angle: 40,
  camera_move: 40,
  content: 300,
  speaker_ref: 32,
  dialogue: 200,
  sfx: 80,
};

type Option = { ref: string; name: string };

export type ShotOptions = {
  /** 场景档案里的 ref → 名称。分镜跑在场景档案之前时可能是空的。 */
  scenes: Option[];
  characters: Option[];
};

const labelClass = "text-[10px] text-fg-subtle";
const inputClass =
  "w-full rounded-md border border-border-strong bg-bg px-2 py-1.5 text-sm text-fg " +
  "transition-colors duration-150 focus:border-primary focus:outline-none " +
  "disabled:cursor-not-allowed disabled:opacity-60";

/** 改过还没保存的字段左边点一颗点。颜色之外还有 title，不只靠颜色传达。 */
function DirtyDot({ on }: { on: boolean }) {
  if (!on) return null;
  return (
    <span
      title="这一项改过，还没保存"
      className="inline-block size-1.5 shrink-0 rounded-full bg-rf-warning align-middle"
    />
  );
}

function FieldLabel({
  htmlFor,
  label,
  dirty,
  count,
  limit,
}: {
  htmlFor?: string;
  label: string;
  dirty: boolean;
  count?: number;
  limit?: number;
}) {
  const over = limit !== undefined && count !== undefined && count > limit;
  return (
    <div className="flex items-center gap-1.5">
      <label htmlFor={htmlFor} className={labelClass}>
        {label}
      </label>
      <DirtyDot on={dirty} />
      {limit !== undefined && count !== undefined && (
        <span
          className={cn("tnum ml-auto text-[10px]", over ? "text-danger" : "text-fg-subtle")}
        >
          {count} / {limit}
        </span>
      )}
    </div>
  );
}

function TextField(props: {
  label: string;
  field: ShotFieldKey;
  value: string;
  dirty: boolean;
  disabled: boolean;
  multiline?: boolean;
  rows?: number;
  suggestions?: Option[] | string[];
  placeholder?: string;
  onChange: (value: string) => void;
}) {
  const id = React.useId();
  const listId = `${id}-list`;
  const { suggestions } = props;
  const limit = LIMIT[props.field];

  const options: { value: string; hint?: string }[] = (suggestions ?? []).map((s) =>
    typeof s === "string" ? { value: s } : { value: s.ref, hint: s.name },
  );

  return (
    <div className="min-w-0">
      <FieldLabel
        htmlFor={id}
        label={props.label}
        dirty={props.dirty}
        count={limit === undefined ? undefined : props.value.length}
        limit={limit}
      />
      {props.multiline ? (
        <textarea
          id={id}
          rows={props.rows ?? 3}
          value={props.value}
          disabled={props.disabled}
          placeholder={props.placeholder}
          onChange={(e) => props.onChange(e.target.value)}
          className={cn(inputClass, "mt-1 resize-y leading-6")}
        />
      ) : (
        <>
          <input
            id={id}
            type="text"
            value={props.value}
            disabled={props.disabled}
            placeholder={props.placeholder}
            list={options.length > 0 ? listId : undefined}
            onChange={(e) => props.onChange(e.target.value)}
            className={cn(inputClass, "mt-1")}
          />
          {options.length > 0 && (
            <datalist id={listId}>
              {options.map((o) => (
                <option key={o.value} value={o.value} label={o.hint} />
              ))}
            </datalist>
          )}
        </>
      )}
    </div>
  );
}

/**
 * 出场人物：一串 ref。
 *
 * 做成"胶囊 + 增删"而不是逗号分隔的文本框——顺序在后端是有意义的
 * （`request_shot_image` 照抄 `character_refs` 的顺序喂给 `compose_shot`），
 * 文本框里用户一不小心就把顺序改了却看不出来。
 */
function RefListField({
  value,
  options,
  dirty,
  disabled,
  onChange,
}: {
  value: string[];
  options: Option[];
  dirty: boolean;
  disabled: boolean;
  onChange: (next: string[]) => void;
}) {
  const id = React.useId();
  const listId = `${id}-list`;
  const [draft, setDraft] = React.useState("");
  const nameOf = React.useMemo(
    () => new Map(options.map((o) => [o.ref, o.name])),
    [options],
  );

  const add = () => {
    const ref = draft.trim();
    // 重复的 ref 后端会照收（schema 不查重），但同一个人出现两次对出图
    // 没有意义，反而会让 compose 的角色顺序失真。
    if (!ref || value.includes(ref)) {
      setDraft("");
      return;
    }
    onChange([...value, ref]);
    setDraft("");
  };

  return (
    <div className="min-w-0">
      <FieldLabel label="出场人物" dirty={dirty} />
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        {value.map((ref, at) => (
          <span
            key={`${ref}-${at}`}
            className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-border bg-surface-2 py-1 pr-1 pl-2.5 text-[11px] text-fg-muted"
          >
            <UserRound aria-hidden className="size-3 shrink-0 text-rf-agent" />
            <span className="min-w-0 truncate">{nameOf.get(ref) ?? ref}</span>
            <button
              type="button"
              disabled={disabled}
              aria-label={`移除 ${nameOf.get(ref) ?? ref}`}
              onClick={() => onChange(value.filter((_, i) => i !== at))}
              className="grid size-4 shrink-0 cursor-pointer place-items-center rounded-full text-fg-subtle transition-colors duration-150 hover:bg-danger-soft hover:text-danger disabled:pointer-events-none disabled:opacity-45"
            >
              <X aria-hidden className="size-3" />
            </button>
          </span>
        ))}
        {value.length === 0 && <span className="text-[11px] text-fg-subtle">这一镜没有人物</span>}
      </div>
      <div className="mt-2 flex items-center gap-1.5">
        <input
          type="text"
          value={draft}
          disabled={disabled}
          list={listId}
          placeholder="角色 ref"
          aria-label="添加出场人物"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== "Enter") return;
            // 这个输入框在表单里，回车默认会提交表单——那不是"添加一个人物"
            e.preventDefault();
            add();
          }}
          className={cn(inputClass, "h-8 flex-1 py-0")}
        />
        <button
          type="button"
          disabled={disabled || !draft.trim()}
          onClick={add}
          className="inline-flex h-8 shrink-0 cursor-pointer items-center gap-1 rounded-md border border-border-strong bg-surface px-2 text-xs text-fg transition-colors duration-150 hover:bg-surface-2 disabled:pointer-events-none disabled:opacity-45"
        >
          <Plus aria-hidden className="size-3.5" />
          添加
        </button>
        <datalist id={listId}>
          {options
            .filter((o) => !value.includes(o.ref))
            .map((o) => (
              <option key={o.ref} value={o.ref} label={o.name} />
            ))}
        </datalist>
      </div>
    </div>
  );
}

/** 只读的一格。序号和节点号在这里，不给编辑——理由见 `ShotDetail` 的说明。 */
function SpecItem({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="min-w-0">
      <dt className={labelClass}>{label}</dt>
      <dd className="tnum mt-1 text-sm break-words text-fg" title={hint}>
        {value}
      </dd>
    </div>
  );
}

/**
 * 单镜详情 + 逐字段编辑。
 *
 * **`index` 和 `node_index` 是只读的**，虽然后端也收它们的 patch：
 * 镜号是出图记录（`tasks.shot_index`）唯一的关联键，改掉它会让这一镜
 * 已经出的图默默挂到另一镜上去，而那个动作没有任何提示、也不在 ADR-029
 * 的撤销能修复的范围内（撤回来的是分镜，图的归属不会跟着回去）。
 * 节点号同理，它是分镜节点覆盖核验的依据。要动这两个只能重跑分镜。
 */
export function ShotDetail(props: {
  shot: ShotCardData & { nodeIndex: number };
  draft: ShotDraft;
  dirty: ReadonlySet<ShotFieldKey>;
  options: ShotOptions;
  disabled: boolean;
  onChange: <K extends ShotFieldKey>(field: K, value: ShotDraft[K]) => void;
  /** 出图这类会花钱的动作，由页面注入 */
  actions: React.ReactNode;
  /** 保存 / 放弃那一条，以及未保存提示 */
  footer?: React.ReactNode;
  /** 这一镜的图比最后一次改动旧时的提示 */
  outdatedNotice?: React.ReactNode;
}) {
  const { shot, draft, dirty, options, disabled, onChange } = props;
  const sceneName = options.scenes.find((s) => s.ref === draft.scene_ref)?.name;

  return (
    <article className="overflow-hidden rounded-2xl border border-border bg-surface shadow-rf-card">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4">
        <div className="flex min-w-0 items-center gap-3">
          <span className="shrink-0 font-mono text-xs text-primary">{shot.code}</span>
          <h2 className="truncate text-sm font-semibold text-fg">{shot.title}</h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">{props.actions}</div>
      </header>

      {/* gap-px + 底色 = 一条分隔线，同时在单列断点下自动消失 */}
      <div className="grid gap-px bg-border lg:grid-cols-[minmax(0,1.15fr)_minmax(16rem,0.85fr)]">
        <div className="min-w-0 bg-surface p-5">
          <div className="relative mb-4 aspect-video overflow-hidden rounded-xl border border-border bg-bg">
            <ShotImage
              src={shot.imageUrl}
              alt={`${shot.code} ${shot.title}`}
              iconClassName="size-10"
            />
          </div>

          {props.outdatedNotice}

          <div className="mt-4 flex flex-col gap-4">
            <TextField
              label="画面内容"
              field="content"
              multiline
              rows={4}
              value={draft.content}
              dirty={dirty.has("content")}
              disabled={disabled}
              placeholder="这一镜画面上发生了什么。不要写风格词——风格由一致性引擎统一注入"
              onChange={(v) => onChange("content", v)}
            />

            <RefListField
              value={draft.character_refs}
              options={options.characters}
              dirty={dirty.has("character_refs")}
              disabled={disabled}
              onChange={(v) => onChange("character_refs", v)}
            />

            <div>
              <TextField
                label="场景"
                field="scene_ref"
                value={draft.scene_ref}
                dirty={dirty.has("scene_ref")}
                disabled={disabled}
                suggestions={options.scenes}
                placeholder="场景 ref"
                onChange={(v) => onChange("scene_ref", v)}
              />
              {sceneName && (
                <p className="mt-1 inline-flex items-center gap-1.5 text-[11px] text-fg-muted">
                  <MapPin aria-hidden className="size-3 shrink-0 text-primary" />
                  {sceneName}
                </p>
              )}
            </div>
          </div>
        </div>

        <div className="min-w-0 bg-surface p-5">
          <div className="flex items-center gap-2 text-[10px] font-semibold tracking-[0.16em] text-fg-subtle uppercase">
            <Camera aria-hidden className="size-3.5 text-primary" />
            镜头参数
          </div>

          <dl className="mt-4 grid grid-cols-2 gap-x-5 gap-y-4">
            <SpecItem
              label="镜号"
              value={shot.index}
              hint="镜号是出图记录的关联键，改它会让已出的图挂到别的镜上，只能重跑分镜"
            />
            <SpecItem
              label="节点"
              value={shot.nodeIndex}
              hint="分镜节点的覆盖核验依据，同样只能重跑分镜"
            />
          </dl>

          <div className="mt-4 flex flex-col gap-4">
            <TextField
              label="景别"
              field="shot_size"
              value={draft.shot_size}
              dirty={dirty.has("shot_size")}
              disabled={disabled}
              suggestions={SHOT_SIZE_SUGGESTIONS}
              onChange={(v) => onChange("shot_size", v)}
            />
            <TextField
              label="角度"
              field="angle"
              value={draft.angle}
              dirty={dirty.has("angle")}
              disabled={disabled}
              placeholder="正面平视居中可留空"
              onChange={(v) => onChange("angle", v)}
            />
            <TextField
              label="运镜"
              field="camera_move"
              value={draft.camera_move}
              dirty={dirty.has("camera_move")}
              disabled={disabled}
              onChange={(v) => onChange("camera_move", v)}
            />
          </div>

          <div className="mt-6 border-t border-border pt-5">
            <p className="flex items-center gap-2 text-[10px] font-semibold tracking-[0.16em] text-fg-subtle uppercase">
              <MessageSquare aria-hidden className="size-3.5 text-rf-agent" />
              台词与声音
            </p>
            <div className="mt-4 flex flex-col gap-4">
              <TextField
                label="说话人"
                field="speaker_ref"
                value={draft.speaker_ref}
                dirty={dirty.has("speaker_ref")}
                disabled={disabled}
                suggestions={options.characters}
                placeholder="角色 ref，无人说话可留空"
                onChange={(v) => onChange("speaker_ref", v)}
              />
              <TextField
                label="台词"
                field="dialogue"
                multiline
                rows={2}
                value={draft.dialogue}
                dirty={dirty.has("dialogue")}
                disabled={disabled}
                onChange={(v) => onChange("dialogue", v)}
              />
              <TextField
                label="音效"
                field="sfx"
                value={draft.sfx}
                dirty={dirty.has("sfx")}
                disabled={disabled}
                onChange={(v) => onChange("sfx", v)}
              />
            </div>
          </div>
        </div>
      </div>

      {props.footer}
    </article>
  );
}

/** 图比最后一次改动旧时的提示。**只标记，不自动重出**（ADR-033 第 4 条）。 */
export function OutdatedNotice({ editedAt }: { editedAt: number }) {
  return (
    <p className="flex items-start gap-2 rounded-md border border-running/25 bg-running-soft px-3 py-2 text-xs leading-5 text-running">
      <RefreshCw aria-hidden className="mt-0.5 size-3.5 shrink-0" />
      <span>
        这张图出在 {new Date(editedAt).toLocaleString("zh-CN")} 的分镜改动之前，可能与当前镜头内容不符。
        要不要重出由你决定——系统不会自动重跑，也不会删掉现有的图。
      </span>
    </p>
  );
}
