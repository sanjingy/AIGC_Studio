"use client";

import type { NamedEntry } from "@/lib/api";

/**
 * 场景档案里两组**具名条目**的读取：固定参照物与光照状态。
 *
 * 后端 2026-09-07（提交 `2b5a84f`）把这两处从扁平形状改成了结构化：
 *
 *   fixed_references  `string[]`          → `[{name, description, origin}]`
 *   lighting          `string`（单个字段）→ `lighting_states[] + default_lighting`
 *
 * **为什么前端还要再规范化一次**，而不是直接信任后端的形状：`agents/schemas.py`
 * 的 `coerce_*` 只在**校验边界**上跑（Agent 产出落库、字段级 PATCH 回写）。
 * 而界面读到的那份不一定经过那道边界——`useProjectState` 在
 * `current_state_json` 缺某一块时会退回 `agent_runs.output_json`，那是**历史
 * 运行原样存下来的**，里面就是改动之前的扁平形状。不规范化，那条兜底路径上
 * 的固定参照物会渲染成 `[object Object]`，光照会渲染成 `undefined`。
 *
 * 这不是"前端又写了一套规则"：规则仍然只有后端那一份，这里做的是**读侧的
 * 形状兼容**，且只有归一化没有校验——什么形状合法由后端 schema 判。
 */

function text(value: unknown): string {
  return value === null || value === undefined ? "" : String(value).trim();
}

/** 名称是从描述里截出来的时候截多长。与后端 `MIGRATED_NAME_CHARS` 同值。 */
const DERIVED_NAME_CHARS = 12;

function derivedName(description: string): string {
  return description.slice(0, DERIVED_NAME_CHARS);
}

/**
 * 把一串条目统一成 `{name, description, origin}`。
 *
 * 三种输入都认，因为三种都真实存在：新产出的对象、存量的纯字符串、
 * 只填了一半的半成品。**任何一种都不丢原文**——缺名称时从描述里截一个，
 * 完整描述仍然在 `description` 里。
 */
export function namedEntriesOf(value: unknown): NamedEntry[] {
  if (!Array.isArray(value)) return [];

  const out: NamedEntry[] = [];
  for (const item of value) {
    if (item && typeof item === "object") {
      const row = item as Record<string, unknown>;
      let name = text(row.name);
      let description = text(row.description);
      if (!name && !description) continue;
      let origin = text(row.origin);
      if (!description) {
        description = name;
        origin ||= "migrated";
      }
      if (!name) {
        name = derivedName(description);
        origin ||= "migrated";
      }
      out.push({ name, description, origin: origin === "migrated" ? "migrated" : "authored" });
      continue;
    }
    const flat = text(item);
    if (!flat) continue;
    out.push({ name: derivedName(flat), description: flat, origin: "migrated" });
  }
  return out;
}

/** 一个场景的固定参照物。空数组是合法的——不是每个场景都钉了东西。 */
export function fixedReferencesOf(scene: unknown): NamedEntry[] {
  if (!scene || typeof scene !== "object") return [];
  return namedEntriesOf((scene as Record<string, unknown>).fixed_references);
}

/**
 * 一个场景的光照状态。**一定至少一条**（后端 `min_length=1`），
 * 存量的单个 `lighting` 字符串在这里迁成一条名为「默认」的状态——
 * 老数据里只有一种光，读出来仍然只有一种，与改动前逐字相同。
 */
export function lightingStatesOf(scene: unknown): NamedEntry[] {
  if (!scene || typeof scene !== "object") return [];
  const row = scene as Record<string, unknown>;
  const states = namedEntriesOf(row.lighting_states);
  if (states.length > 0) return states;

  const legacy = text(row.lighting);
  if (!legacy) return [];
  return [{ name: DEFAULT_LIGHTING_NAME, description: legacy, origin: "migrated" }];
}

/** 与后端 `DEFAULT_LIGHTING_NAME` 同值。存量场景迁过来的那一条叫这个名字。 */
export const DEFAULT_LIGHTING_NAME = "默认";

/**
 * 这个场景没写 `lighting_ref` 的镜头用哪一个光照状态。
 *
 * 后端保证 `default_lighting` 一定指向已声明的状态之一；这里再兜一层，
 * 因为兜底路径读到的历史产出没走过那道保证。指不到就落到第一个状态——
 * 与后端 `_accept_legacy_shape` 的行为一致，绝不留成未定义。
 */
export function defaultLightingOf(scene: unknown): string {
  const states = lightingStatesOf(scene);
  if (states.length === 0) return "";
  const declared = text((scene as Record<string, unknown>)?.default_lighting);
  if (declared && states.some((s) => s.name === declared)) return declared;
  return states[0]!.name;
}
