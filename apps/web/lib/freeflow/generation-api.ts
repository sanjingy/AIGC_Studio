import { apiFetch, ApiRequestError } from "@/lib/api";

export type PromptKind = "character" | "scene" | "shot_image" | "shot_video";
export type PromptDraft = {
  run_id: string;
  kind: PromptKind;
  subject_key: string;
  prompt: string;
  negative_prompt: string;
  basis_digest: string;
  stale: boolean;
  rule_version: string;
  agent_id: string;
  model_id: string | null;
  created_at: string;
};

export type GenerationRecord = {
  id: string;
  record_type: "agent" | "image";
  subject_kind: string | null;
  subject_key: string | null;
  title: string;
  status: string;
  agent_id: string | null;
  model_id: string | null;
  source: string | null;
  created_at: string;
  finished_at: string | null;
  error_code: string | null;
  asset_ids: string[];
};

export type GenerationDetail = GenerationRecord & {
  user_input: string | null;
  prompt: string | null;
  negative_prompt: string | null;
  actual_prompt: string | null;
  rule_version: string | null;
  basis_digest: string | null;
  incomplete: boolean;
  steps: { index: number; kind: string; duration_ms: number; error: string | null; raw_output: string | null }[];
  output: Record<string, unknown> | null;
};

function promptPath(projectId: string, kind: PromptKind, subjectKey: string) {
  return `/projects/${encodeURIComponent(projectId)}/prompts/${kind}/${encodeURIComponent(subjectKey)}`;
}

export const generation = {
  prompt: (projectId: string, kind: PromptKind, subjectKey: string, signal?: AbortSignal) =>
    apiFetch<PromptDraft | null>(promptPath(projectId, kind, subjectKey), { signal }),
  prepare: (projectId: string, kind: PromptKind, subjectKey: string, instruction: string) =>
    apiFetch<PromptDraft>(promptPath(projectId, kind, subjectKey), {
      method: "POST", body: JSON.stringify({ instruction }),
    }),
  records: (projectId: string, signal?: AbortSignal) =>
    apiFetch<GenerationRecord[]>(`/projects/${encodeURIComponent(projectId)}/generation-records?limit=100`, { signal }),
  detail: (projectId: string, type: GenerationRecord["record_type"], id: string, signal?: AbortSignal) =>
    apiFetch<GenerationDetail>(`/projects/${encodeURIComponent(projectId)}/generation-records/${type}/${encodeURIComponent(id)}`, { signal }),
};

export function generationError(error: unknown): string {
  return error instanceof ApiRequestError
    ? error.error.user_message || error.message
    : "暂时无法读取生成信息，请稍后重试。";
}

/**
 * 这条记录是不是"测试占位结果"。
 *
 * 没配 Provider Key 时，文本退回 `MockLLM`（`mock.llm.v1`）、出图退回占位图
 * （`mock.image.v1`）——两条都会照常扣 Credits、照常在记录里留一行，产物却
 * 不是真实模型画/写出来的。不标出来，用户回看历史时无法区分"这版效果不好"
 * 和"这版根本没调模型"。
 *
 * 判前缀而不是列举两个具体 id：后端再加一种 mock 能力时，界面不用跟着发版。
 */
export function isPlaceholderModel(modelId: string | null | undefined): boolean {
  return typeof modelId === "string" && modelId.startsWith("mock.");
}

export const PLACEHOLDER_LABEL = "测试占位结果";
