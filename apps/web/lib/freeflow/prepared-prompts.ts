"use client";

import { useCallback, useSyncExternalStore } from "react";

import type { PromptDraft, PromptKind } from "./generation-api";

/**
 * 「用户这次准备好的那份提示词」的落点。
 *
 * 出图请求可以带一个可选的 `prompt_run_id`（见 `lib/api.ts` 的三个
 * render 入口）。带上它，后端用的就是用户**刚刚在面板里看过的那一份**；
 * 不带，后端会自己准备一份符合当前输入的。两条都走新的提示词 Agent，
 * 区别只在于"用户看到的词"和"实际出图的词"是不是同一份。
 *
 * 为什么要一个模块级的小仓库，而不是把 run_id 从面板一路 props 传出去：
 * 查看提示词的 `PromptPanel` 和按下出图的那颗按钮，在三处入口里的相对
 * 位置都不一样——角色/场景在 `RenderSlot` 内部并排，分镜在 `ShotEditor`
 * 的 actions 里，批量出图那颗按钮则在页面顶部、离面板十万八千里。
 * 走 props 要穿过三条不同的链路，漏一条就退回"看到的词和出的图对不上"。
 *
 * 键是 `项目 + 提示词类型 + 对象`，三段缺一不可：
 *
 * - 带项目：切到另一个项目不会把上一个项目的 run_id 带过去（后端也会
 *   拒绝，但那是一次没必要的失败请求）。
 * - 带类型：`shot_image` 和 `shot_video` 的 subject_key 都是镜号。不分开
 *   的话，用户准备了一次视频提示词，再点「生成首帧图」就会把视频词送去出图。
 * - 带对象：切换角色/镜头不串词。
 *
 * **只记不过期的那一份。** `stale=true` 的提示词后端本来就会拒绝，
 * 记下来只会把"档案改过了"变成一次失败请求；直接忘掉，出图时退回自动准备。
 */

type Entry = { runId: string; createdAt: number };

const entries = new Map<string, Entry>();
const listeners = new Set<() => void>();

/** 分隔符用换行：ref 和镜号里都不会出现它，拼出来的键不会歧义。 */
function keyOf(projectId: string, kind: PromptKind, subjectKey: string): string {
  return [projectId, kind, subjectKey].join("\n");
}

function emit() {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * 记住用户刚看过 / 刚准备好的这一份。
 *
 * `draft` 为 null（还没准备过）或已过期时是**遗忘**，不是"保持原样"——
 * 面板重新读到一份过期的词，意味着上游档案改了，旧 run_id 不能再用。
 */
export function rememberPrompt(
  projectId: string,
  kind: PromptKind,
  subjectKey: string,
  draft: PromptDraft | null,
): void {
  const key = keyOf(projectId, kind, subjectKey);
  const previous = entries.get(key);
  if (!draft || draft.stale) {
    if (!previous) return;
    entries.delete(key);
    emit();
    return;
  }
  if (previous?.runId === draft.run_id) return;
  entries.set(key, { runId: draft.run_id, createdAt: Date.parse(draft.created_at) });
  emit();
}

export function forgetPrompt(projectId: string, kind: PromptKind, subjectKey: string): void {
  if (!entries.delete(keyOf(projectId, kind, subjectKey))) return;
  emit();
}

/**
 * 内容比提示词新 → 忘掉这一份。
 *
 * 字段级编辑（ADR-029）只改 `current_state_json`，不会碰任何一条提示词
 * 运行记录，所以"这份词还算不算数"只能靠两个时间戳比出来——和分镜卡片
 * 判断"图是不是过期"用的是同一招（`storyboard-editor.tsx` 的 `outdated`）。
 *
 * 比不出来（`created_at` 解析不了）时保留：后端还会再校验一次过期，
 * 前端这一层只是省掉一次注定失败的请求，不该反过来把好的那份丢掉。
 */
export function dropPromptEditedBefore(
  projectId: string,
  kind: PromptKind,
  subjectKey: string,
  editedAt: number | null,
): void {
  if (editedAt === null) return;
  const key = keyOf(projectId, kind, subjectKey);
  const entry = entries.get(key);
  if (!entry || !Number.isFinite(entry.createdAt) || entry.createdAt >= editedAt) return;
  entries.delete(key);
  emit();
}

/**
 * 出图那一刻取一次。`useRenders` 在发请求时调它，不走 React 状态——
 * 面板刚点完「准备」、用户马上点出图，取到的必须是最新那一份。
 */
export function preparedPromptRunId(
  projectId: string,
  kind: PromptKind,
  subjectKey: string,
): string | null {
  return entries.get(keyOf(projectId, kind, subjectKey))?.runId ?? null;
}

/** 界面上要显示"这次会用你刚看过的那份词"时用。 */
export function usePreparedPrompt(
  projectId: string | null,
  kind: PromptKind,
  subjectKey: string,
): string | null {
  const snapshot = useCallback(
    () => (projectId ? preparedPromptRunId(projectId, kind, subjectKey) : null),
    [projectId, kind, subjectKey],
  );
  return useSyncExternalStore(subscribe, snapshot, () => null);
}
