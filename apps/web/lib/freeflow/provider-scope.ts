import { connectionLabel, normalizeApiAddress } from "./model-options";
/**
 * 模型与供应商页（ADR-039 A2）的纯逻辑：引用串、测试结论、默认切换的行、表单草稿与请求体。
 *
 * 不依赖 React 与 `@/` 别名，`tests/web-logic.test.mjs` 直接加载。
 *
 * 两条贯穿全文件的约束：
 * - **Key 明文只活在草稿里。** 请求体里 Key 为空就不带这个键（PATCH 沿用原 Key），
 *   任何展示文案只用后端给的掩码。
 * - **能力由协议决定，不由前端推。** 协议 → 能力的对应只读 `/model-config/presets` 的
 *   `protocols`，前端不另存一张表。
 */
import type {
  CapabilityConfig,
  ConnectionCreate,
  ConnectionModelInput,
  ConnectionPatch,
  ConnectionReferences,
  KeyTestResult,
  ProtocolSpec,
  ProviderConnection,
  ProviderPreset,
} from "../api";

export const ORG_PREFIX = "provider.org:";

// ---------------------------------------------------------------- 引用串

/** 项目偏好里指向连接模型的值：`provider.org:<id>:<model>`；不带模型 = 连接里该能力的第一个 */
export function orgRef(connectionId: string, modelId?: string | null): string {
  return modelId ? `${ORG_PREFIX}${connectionId}:${modelId}` : `${ORG_PREFIX}${connectionId}`;
}

/** 不是连接引用时返回 null。模型 id 里可能还有冒号，只切第一个 */
export function parseOrgRef(value: string | null | undefined): { connectionId: string; modelId: string | null } | null {
  if (!value || !value.startsWith(ORG_PREFIX)) return null;
  const rest = value.slice(ORG_PREFIX.length);
  const at = rest.indexOf(":");
  if (at < 0) return rest ? { connectionId: rest, modelId: null } : null;
  const connectionId = rest.slice(0, at);
  const modelId = rest.slice(at + 1);
  return connectionId ? { connectionId, modelId: modelId || null } : null;
}

// ---------------------------------------------------------------- 测试连接的三种结论

export type TestVerdict = "pass" | "neutral" | "fail";

/**
 * 测试结论。**中性不是失败**：出图上游不少不实现 `/models`，后端会给 ok=true 加一句
 * 「以第一次出图为准」；上游限流时 Key 多半是好的，只是此刻验证不了。这两种画成红色，
 * 用户会去换一把本来没问题的 Key。
 *
 * 后端没有单独的字段表达"无法验证"，只能读它给的原话；改了措辞这里会退回 pass，
 * 不会把它误判成失败。
 */
export function classifyTest(result: Pick<KeyTestResult, "ok" | "message" | "error_code">): TestVerdict {
  if (result.ok) {
    return /以第一次|无法(免费)?验证|未能验证|不能验证/.test(result.message) ? "neutral" : "pass";
  }
  return result.error_code === "provider.rate_limit.exceeded" ? "neutral" : "fail";
}

export const VERDICT_LABEL: Record<TestVerdict, string> = {
  pass: "连接正常",
  neutral: "无法免费验证",
  fail: "连接失败",
};

// ---------------------------------------------------------------- 错误原因

const BROKEN_TEXT: Record<string, string> = {
  connection_missing: "指向的供应商已被删除",
  connection_disabled: "指向的供应商已停用",
  capability_mismatch: "指向的供应商里没有这个能力的模型",
  model_missing: "指向的模型已从供应商里移除",
};

/** 组织默认坏掉的原因码 → 人话。未知码原样给出，不吞 */
export function brokenReasonText(reason: string | null | undefined): string | null {
  if (!reason) return null;
  return BROKEN_TEXT[reason] ?? `默认设置已失效（${reason}）`;
}

const BYOK_REASON: Record<string, string> = {
  ...BROKEN_TEXT,
  connection_missing: "选中的供应商已被删除",
  connection_disabled: "选中的供应商已停用",
  model_missing: "选中的模型已从供应商里移除",
  reasoning_model_not_allowed: "选中的是推理模型，流程中的分类、结构化环节不能用推理模型",
};

export function byokReasonText(reason: unknown): string | null {
  return typeof reason === "string" && reason ? (BYOK_REASON[reason] ?? null) : null;
}

/** 每个能力在平台这边的默认生成接口（`gateway/catalog.py::PROTOCOLS`）；文本另有 Responses，按实际接口判 */
const CHAT_API = "OpenAI Chat Completions（POST /chat/completions）";
const GENERATION_API: Record<string, string> = {
  text_generation: CHAT_API,
  image_generation: "OpenAI Images（POST /images/generations）",
};
const RESPONSES_API = "OpenAI Responses（POST /responses）";

const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

/** 这次失败实际调的是哪个接口：看 `operation` 的路径，认不出再按能力给默认 */
function generationApiOf(d: Record<string, unknown>): string {
  const op = str(d.operation) ?? "";
  if (/\/responses$/.test(op)) return RESPONSES_API;
  if (/\/chat\/completions$/.test(op)) return CHAT_API;
  return GENERATION_API[str(d.capability) ?? ""] ?? "平台支持的 OpenAI 兼容接口";
}

/**
 * 文本 404 时的下一步：Chat 与 Responses 互为另一条路。改协议在已保存连接上做，Key 沿用，
 * 不用重填——真实案例 cchost + gpt-6.1-sol 只挂在 Responses 上（CC Switch 里 API 格式即 OpenAI Responses）。
 */
function switchProtocolHint(op: string): string {
  if (/\/chat\/completions$/.test(op)) {
    return `。如果供应商说明该模型走 OpenAI Responses（例如 CC Switch 里这个供应商的 API 格式是「OpenAI Responses」），到「模型」页编辑这个供应商，把「用途」改成「${USAGE_LABEL.openai_responses}」后保存，API Key 留空即沿用原 Key`;
  }
  if (/\/responses$/.test(op)) {
    return `。如果供应商只提供 Chat Completions，到「模型」页编辑这个供应商，把「用途」改回「${USAGE_LABEL.openai_chat}」，API Key 留空即沿用原 Key`;
  }
  return "";
}

/**
 * 自带上游调用失败时**失败在哪一层**（后端 `service._upstream_reason`）。
 *
 * 真实踩过：中转 `GET /models` 鉴权通过、列出了模型，`POST /chat/completions` 回 404，
 * 界面却说"你的 API Key 调用失败"，用户去换了一把本来没问题的 Key。所以只有 401/403
 * 才说 Key 被拒；404 说清是哪个接口、哪个模型对不上，并给出能做的事。认不出的码返回 null。
 */
export function upstreamFailureText(detail: Record<string, unknown> | undefined): string | null {
  const d = detail ?? {};
  const status = typeof d.http_status === "number" ? d.http_status : null;
  // 后面总是紧跟中文，模型 ID 两侧留空格
  const model = str(d.model_id) ? `模型 ${d.model_id} ` : "所选模型";
  const op = str(d.operation) ?? "生成接口";
  const code = str(d.upstream_error_code);
  const api = generationApiOf(d);
  const http = status ? `HTTP ${status}${code ? ` ${code}` : ""}` : null;
  switch (str(d.reason)) {
    case "upstream_not_found":
      return (
        `供应商对 ${op} 返回 ${http}：这个地址上没有该接口，或${model}不能通过它调用。` +
        `这通常不是 API Key 的问题——测试连接只读取模型列表，列表里有这个模型不代表它支持该接口。` +
        `平台调用的是 ${api}；请向供应商确认${model}支持这个接口，或在「模型」页换一个模型` +
        switchProtocolHint(op)
      );
    case "upstream_model_not_found":
      return `供应商说${model}不存在（${http}）。请在「模型」页核对模型 ID，或换一个模型`;
    case "upstream_auth_rejected":
      return `供应商拒绝了你的 API Key${http ? `（${http}）` : ""}：Key 无效、已过期，或没有调用${model}的权限。请在「模型」页更换 Key`;
    case "upstream_quota_exhausted":
      return `你在供应商处的余额或额度不足${http ? `（${http}）` : ""}，请到供应商充值后再试`;
    case "upstream_rate_limited":
      return "供应商限流，请稍后再试";
    case "upstream_request_rejected":
      return `供应商不接受这次请求${http ? `（${http}）` : ""}：${model}可能不支持 ${api} 的某些参数。可在「模型」页换一个模型`;
    case "upstream_redirect":
      return `供应商返回了跳转${http ? `（${http}）` : ""}，出于安全不跟随。请在「模型」页把请求地址改成最终地址`;
    case "upstream_address_rejected":
      return "请求地址解析到了内网或保留地址，已拒绝请求。请在「模型」页检查请求地址";
    case "upstream_content_rejected":
      return "内容被供应商的安全策略拦下，改一下描述再试";
    case "upstream_timeout":
      return "供应商响应超时，请稍后再试";
    case "upstream_unavailable":
      return `供应商暂时不可用${http ? `（${http}）` : ""}，请稍后再试；一直这样请在「模型」页检查请求地址`;
    default:
      return null;
  }
}

// ---------------------------------------------------------------- 一个模型都没有

/**
 * 这个能力一个候选模型都没有（平台没配 Key、组织也没接供应商）。等多久都不会好，
 * 只能去模型库配——所以界面遇到它给「去模型库」，不把「重试」当主按钮。
 * 一律按错误码判断，不匹配文案。
 */
export const NOT_CONFIGURED = "provider.not_configured";
export const MODELS_HREF = "/freeflow/models";

const CAPABILITY_LABEL: Record<string, string> = {
  text_generation: "文本生成",
  image_generation: "图片生成",
};

export function needsModelSetup(code: string | null | undefined): boolean {
  return code === NOT_CONFIGURED;
}

/** 后端 `user_message` 不带能力名，能力在 `detail.capability`。认不出的能力返回 null，用后端原句。 */
export function notConfiguredText(capability: unknown): string | null {
  const label = typeof capability === "string" ? CAPABILITY_LABEL[capability] : undefined;
  return label ? `还没有可用的${label}模型：请到「模型库」添加供应商并设为默认，或联系管理员配置平台 Key` : null;
}

type ErrorLike = { code: string; message?: string; user_message?: string; detail?: Record<string, unknown> };

/**
 * 一条接口错误给用户看的话。`provider.byok.rejected` 的通用文案只说"供应商调用失败"，
 * 连接删了 / 停了 / 选了推理模型、上游回 404 等都不是 Key 的问题，按 `detail.reason` 说具体。
 * `preferMessage` 用在设置页：那里要显示后端原文（含字段原因），不是笼统的一句。
 */
export function describeApiError(error: ErrorLike, preferMessage = false): string {
  const base = (preferMessage ? error.message || error.user_message : error.user_message || error.message) ?? "";
  if (error.code === NOT_CONFIGURED) return notConfiguredText(error.detail?.capability) ?? base;
  if (error.code !== "provider.byok.rejected") return base;
  const reason = byokReasonText(error.detail?.reason);
  if (reason) return `${reason}。去「模型」页或项目设置改选。`;
  const upstream = upstreamFailureText(error.detail);
  return upstream ? `${upstream}。` : base;
}

// ---------------------------------------------------------------- 一键切换默认

export type DefaultRow =
  | {
      kind: "catalog";
      key: string;
      providerId: string;
      label: string;
      current: boolean;
      available: boolean;
      unavailableReason: string | null;
    }
  | {
      kind: "org";
      key: string;
      providerId: string;
      connectionId: string;
      label: string;
      modelId: string;
      current: boolean;
      available: boolean;
      unavailableReason: string | null;
      /** 只在 false 时标「未实测」 */
      unverified: boolean;
      reasoning: boolean;
    };

/**
 * 一个能力的「设为默认」候选：目录里的每一家一行（模型与计费在行内选），
 * 每个连接里这个能力的每个模型一行（一键）。
 *
 * 连接行的模型以聚合视图 `providers[].models` 为准（只列该能力的模型）；
 * 推理标记聚合视图不带，从连接列表里补。
 */
export function defaultRows(item: CapabilityConfig, connections: ProviderConnection[]): DefaultRow[] {
  const sel = item.selection;
  const rows: DefaultRow[] = [];
  for (const p of item.providers) {
    if (p.kind === "catalog") {
      rows.push({
        kind: "catalog",
        key: p.provider_id,
        providerId: p.provider_id,
        label: p.label,
        current: sel?.layer === "org" && sel.provider_id === p.provider_id,
        available: p.available,
        unavailableReason: p.unavailable_reason,
      });
      continue;
    }
    const connectionId = p.connection_id ?? p.provider_id.slice(ORG_PREFIX.length);
    const conn = connections.find((c) => c.id === connectionId);
    const first = p.models[0]?.model_id ?? null;
    for (const m of p.models) {
      const savedModel = sel?.model_id ?? first;
      rows.push({
        kind: "org",
        key: `${p.provider_id}|${m.model_id}`,
        providerId: p.provider_id,
        connectionId,
        label: p.label,
        modelId: m.model_id,
        current: sel?.layer === "org" && sel.provider_id === p.provider_id && savedModel === m.model_id,
        available: p.available,
        unavailableReason: p.unavailable_reason,
        unverified: p.consistency_verified === false,
        reasoning: Boolean(conn?.models.find((x) => x.model_id === m.model_id && x.capability === item.capability)?.reasoning),
      });
    }
  }
  return rows;
}

/** 当前默认的一句话：「平台 · DeepSeek · 目录默认顺序」/「供应商 公司网关 · gpt-4o」 */
export function currentDefaultText(item: CapabilityConfig): string {
  const sel = item.selection;
  if (!sel || sel.layer !== "org") {
    const first = item.providers.find((p) => p.kind === "catalog");
    return `未设置，使用平台默认${first ? `（${first.label}）` : ""}`;
  }
  const p = item.providers.find((x) => x.provider_id === sel.provider_id);
  if (sel.provider_id?.startsWith(ORG_PREFIX)) {
    const model = sel.model_id ?? p?.models[0]?.model_id ?? "第一个模型";
    return p ? `供应商 ${p.label} · ${model}` : `已删除的供应商 · ${model}`;
  }
  const label = p?.label ?? sel.provider_id;
  return `平台 ${label} · ${sel.model_id ?? "目录默认顺序"} · ${sel.key_source === "org" ? `我的 ${label} 官方 Key` : "平台额度"}`;
}

// ---------------------------------------------------------------- 平台行的计费来源

export type BillingChoice = {
  label: string;
  disabled: boolean;
  /** 为什么点不了；可点时为 null。界面要把它写出来，不能只给一个灰掉的圆点 */
  reason: string | null;
};

/**
 * 平台目录行里两个计费来源的文案与可用性。
 *
 * 「自有」这一项指的是**平台目录里这家的官方 Key**（`provider_credentials`，按能力 +
 * provider 存一把），不是用户在「供应商」里添加的连接——连接在「我的供应商」里单独设为默认。
 * 两者名字都像"我的 Key"，用户添加了连接后会以为这个灰掉的圆点就是它，所以标签必须
 * 带上供应商名和「官方」，禁用原因要写明它和连接是两回事。
 *
 * 禁用规则不放宽：没存这家的官方 Key 就不能选它计费（后端会拒）。
 */
export function billingChoices(
  item: Pick<CapabilityConfig, "configurable">,
  provider: { label: string; supports_platform_key: boolean },
  hasOwnKey: boolean,
): { platform: BillingChoice; own: BillingChoice } {
  const locked = !item.configurable ? "当前 Skill 不允许改这个能力的上游" : null;
  return {
    platform: {
      label: "平台额度",
      disabled: Boolean(locked) || !provider.supports_platform_key,
      reason: locked ?? (provider.supports_platform_key ? null : `平台没有提供 ${provider.label} 的额度`),
    },
    own: {
      label: `我的 ${provider.label} 官方 Key`,
      disabled: Boolean(locked) || !hasOwnKey,
      reason:
        locked ??
        (hasOwnKey
          ? null
          : `还没保存 ${provider.label} 官方 Key，先点「配置 ${provider.label} Key」。在「供应商」里添加的连接不算，它在下方「我的供应商」里设为默认`),
    },
  };
}

// ---------------------------------------------------------------- 表单草稿

export type ModelDraft = { model_id: string; protocol: string; reasoning: boolean };

export type ConnectionDraft = {
  presetId: string | null;
  label: string;
  baseUrl: string;
  apiKey: string;
  models: ModelDraft[];
};

/**
 * 表单「用途」的三个选项：协议 → 能力只认后端白名单，这里只给常用的三个起个人话名字。
 * `/models` 不说明模型走哪个接口，所以文本的 Chat / Responses 由用户按供应商文档选，前端不猜。
 */
export const USAGE_LABEL: Record<string, string> = {
  openai_chat: "文本生成 · Chat Completions",
  openai_responses: "文本生成 · Responses",
  openai_images: "图片生成",
};
export const USAGE_PROTOCOLS = ["openai_chat", "openai_responses", "openai_images"] as const;

/**
 * 地址里唯一可靠的协议线索：用户粘的是 `.../responses` 完整请求地址。只认这一条，
 * 不按模型名或主机名猜（猜错 = 第一次生成就失败，还是花用户的钱）。
 */
export function protocolHintFromAddress(raw: string): "openai_responses" | null {
  return /\/responses\/*$/i.test(raw.trim()) ? "openai_responses" : null;
}

/**
 * 改地址。**只对新建草稿**、且第一个模型还是默认的 Chat 时，按地址线索改成 Responses；
 * 已保存连接的协议从不因为改地址而变（旧连接不能被静默改写，见 ADR-039 后续决定）。
 */
export function withAddress(draft: ConnectionDraft, baseUrl: string, isNew: boolean): ConnectionDraft {
  const next = { ...draft, baseUrl };
  const hint = protocolHintFromAddress(baseUrl);
  if (!isNew || !hint || draft.models[0]?.protocol !== "openai_chat") return next;
  return { ...next, models: draft.models.map((m, i) => (i === 0 ? { ...m, protocol: hint } : m)) };
}

export function draftFromPreset(preset: ProviderPreset | null, protocols: ProtocolSpec[]): ConnectionDraft {
  if (!preset) {
    return {
      presetId: null,
      label: "",
      baseUrl: "",
      apiKey: "",
      models: [{ model_id: "", protocol: "openai_chat", reasoning: false }],
    };
  }
  return {
    presetId: preset.preset_id,
    label: preset.label,
    baseUrl: preset.base_url,
    apiKey: "",
    models: preset.models.map((m) => ({ model_id: m.model_id, protocol: m.protocol, reasoning: Boolean(m.reasoning) })),
  };
}

/** 编辑：Key 永远从空开始，不回显 */
export function draftFromConnection(conn: ProviderConnection): ConnectionDraft {
  return {
    presetId: conn.preset_id,
    label: conn.label,
    baseUrl: conn.base_url,
    apiKey: "",
    models: conn.models.map((m) => ({ model_id: m.model_id, protocol: m.protocol, reasoning: Boolean(m.reasoning) })),
  };
}

function cleanModels(models: ModelDraft[]): ConnectionModelInput[] {
  return models
    .map((m) => ({ model_id: m.model_id.trim(), protocol: m.protocol, reasoning: m.reasoning }))
    .filter((m) => m.model_id);
}

/**
 * 前端轻校验：只拦"一看就发不出去"的（空、含空白、非 https、数量），
 * 私网 / 元数据 / DNS 这些交给后端——它是唯一的判定。
 */
export function draftProblems(draft: ConnectionDraft, mode: "create" | "edit"): string[] {
  const out: string[] = [];
  const url = draft.baseUrl.trim();
  if (!url) out.push("填写 Base URL");
  else if (!/^https:\/\//i.test(url)) out.push("Base URL 必须以 https:// 开头");
  const key = draft.apiKey.trim();
  if (mode === "create" && !key) out.push("填写 API Key");
  else if (key && (key.length < 8 || key.length > 512)) out.push("API Key 长度应在 8–512 之间");
  const models = cleanModels(draft.models);
  if (!models.length) out.push("至少填一个模型");
  if (models.length > 50) out.push("最多 50 个模型");
  if (draft.models.some((m) => /\s/.test(m.model_id.trim()))) out.push("模型 ID 不能含空白");
  if (models.some((m) => !m.protocol)) out.push("每个模型都要选协议");
  const ids = models.map((m) => `${m.protocol}|${m.model_id}`);
  if (new Set(ids).size !== ids.length) out.push("模型重复");
  return out;
}

export function createBody(draft: ConnectionDraft): ConnectionCreate {
  const body: ConnectionCreate = {
    label: connectionLabel(draft.label, draft.baseUrl),
    base_url: normalizeApiAddress(draft.baseUrl),
    models: cleanModels(draft.models),
    api_key: draft.apiKey.trim(),
  };
  if (draft.presetId) body.preset_id = draft.presetId;
  return body;
}

/** 只发改过的字段；Key 为空不发（沿用原 Key）。没有改动时返回空对象 */
export function patchBody(draft: ConnectionDraft, saved: ProviderConnection): ConnectionPatch {
  const body: ConnectionPatch = {};
  if (draft.label.trim() !== saved.label) body.label = draft.label.trim();
  if (normalizeApiAddress(draft.baseUrl) !== saved.base_url) body.base_url = normalizeApiAddress(draft.baseUrl);
  const next = cleanModels(draft.models);
  const prev = saved.models.map((m) => ({ model_id: m.model_id, protocol: m.protocol, reasoning: Boolean(m.reasoning) }));
  if (JSON.stringify(next) !== JSON.stringify(prev)) body.models = next;
  const key = draft.apiKey.trim();
  if (key) body.api_key = key;
  return body;
}

/** 草稿测试连接的参数：测第一个模型。缺项返回 null（按钮置灰） */
export function draftTestBody(
  draft: ConnectionDraft,
): { protocol: string; model_id: string; base_url: string; api_key: string; preset_id?: string } | null {
  const model = cleanModels(draft.models)[0];
  const key = draft.apiKey.trim();
  const url = draft.baseUrl.trim();
  if (!model || !key || !url) return null;
  return {
    protocol: model.protocol,
    model_id: model.model_id,
    base_url: normalizeApiAddress(url),
    api_key: key,
    ...(draft.presetId ? { preset_id: draft.presetId } : {}),
  };
}

/** 已保存连接的测试参数：只带改过的地址 / Key 与第一个模型 */
export function savedTestBody(
  draft: ConnectionDraft,
  saved: ProviderConnection,
): { protocol?: string; model_id?: string; base_url?: string; api_key?: string } {
  const model = cleanModels(draft.models)[0];
  const body: { protocol?: string; model_id?: string; base_url?: string; api_key?: string } = {};
  if (model) {
    body.protocol = model.protocol;
    body.model_id = model.model_id;
  }
  if (draft.baseUrl.trim() && normalizeApiAddress(draft.baseUrl) !== saved.base_url) body.base_url = normalizeApiAddress(draft.baseUrl);
  if (draft.apiKey.trim()) body.api_key = draft.apiKey.trim();
  return body;
}

// ---------------------------------------------------------------- 删除确认

export function referenceLines(refs: ConnectionReferences, capabilityLabel: (c: string) => string): string[] {
  return [
    ...refs.defaults.map((d) => `组织默认 · ${capabilityLabel(d.capability)}`),
    ...refs.projects.map((p) => `项目「${p.name}」· ${capabilityLabel(p.capability)}`),
  ];
}

/** 连接的来源：预设名或「自定义」。预设已下架时照实写 id */
export function sourceText(conn: Pick<ProviderConnection, "preset_id">, presets: ProviderPreset[]): string {
  if (!conn.preset_id) return "自定义";
  return `预设 · ${presets.find((p) => p.preset_id === conn.preset_id)?.label ?? conn.preset_id}`;
}
