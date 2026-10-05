/**
 * 生成记录的规整。纯函数，不依赖 React / 路径别名，`node --test` 直接测。
 * 类型只用 `import type`，转译后不会真的去加载 `generation-api.ts`（它依赖 `@/`）。
 */
import type { GenerationDetail, GenerationRecord } from "./generation-api";

function text(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

/**
 * 一条记录缺字段时给可读的空值，不让一行坏数据把整本账打成白屏。
 * 早期的提示词运行、迁移补的记录都可能少 title / 时间 / asset_ids。
 * 没有 id 或类型认不出的行没法打开详情，丢掉。
 */
export function normalizeRecord(raw: unknown): GenerationRecord | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const id = text(r.id);
  const type = r.record_type === "agent" || r.record_type === "image" ? r.record_type : null;
  if (!id || !type) return null;
  return {
    id,
    record_type: type,
    subject_kind: text(r.subject_kind),
    subject_key: text(r.subject_key),
    title: text(r.title) ?? (type === "image" ? "出图" : "文本步骤"),
    status: text(r.status) ?? "unknown",
    agent_id: text(r.agent_id),
    model_id: text(r.model_id),
    source: text(r.source),
    created_at: text(r.created_at),
    finished_at: text(r.finished_at),
    error_code: text(r.error_code),
    asset_ids: Array.isArray(r.asset_ids) ? r.asset_ids.filter((a): a is string => typeof a === "string") : [],
  };
}

export function normalizeRecords(raw: unknown): GenerationRecord[] {
  return Array.isArray(raw) ? raw.map(normalizeRecord).filter((r): r is GenerationRecord => r !== null) : [];
}

/** 详情同理：列表字段不是数组当空，文本字段缺了当 null（界面已有「未记录」文案）。 */
export function normalizeDetail(raw: unknown): GenerationDetail | null {
  const base = normalizeRecord(raw);
  if (!base) return null;
  const r = raw as Record<string, unknown>;
  const steps = Array.isArray(r.steps) ? r.steps : [];
  return {
    ...base,
    user_input: text(r.user_input),
    prompt: text(r.prompt),
    negative_prompt: text(r.negative_prompt),
    actual_prompt: text(r.actual_prompt),
    rule_version: text(r.rule_version),
    basis_digest: text(r.basis_digest),
    incomplete: r.incomplete === true,
    steps: steps
      .filter((s): s is Record<string, unknown> => Boolean(s) && typeof s === "object")
      .map((s, i) => ({
        index: typeof s.index === "number" ? s.index : i,
        kind: text(s.kind) ?? "call",
        duration_ms: typeof s.duration_ms === "number" ? s.duration_ms : 0,
        error: text(s.error),
        raw_output: text(s.raw_output),
      })),
    output: r.output && typeof r.output === "object" ? (r.output as Record<string, unknown>) : null,
  };
}
