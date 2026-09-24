"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, Eye, EyeOff, Loader2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  ApiRequestError,
  modelConfig,
  providerCredentials,
  type CapabilityConfig,
  type KeyTestResult,
  type ModelConfig,
  type ProviderOption,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 05 模型（组织层）。
 *
 * 每个已接入能力一本账：**上游 → 具体模型 → 计费来源 → 保存**，外加这家的 Key
 * 与（仅文本）OpenAI 兼容自定义端点。全部来自 `/model-config`：
 *
 * - **选项不在 React 里写死。** 有哪几家、每家哪些模型、自定义端点能不能选，
 *   都是后端目录给的；前端复制一份，迟早会存下一个 Gateway 根本不认识的组合。
 * - **保存的就是 Gateway 用的。** 这里存的是组织默认（`org_model_defaults`），
 *   解析顺序是 项目选择 > 组织默认 > 平台目录默认；Provider 与模型对不上、
 *   选了自有计费却没存 Key，后端直接拒绝，界面照实显示原因。
 * - **没接入的能力不给下拉框。** 视频和语音照实说原因，不放选了不生效的控件。
 * - **Key 明文只在输入框里活过一次。** 保存后只剩后端给的尾号。
 */
export function ModelCatalogPage() {
  const [config, setConfig] = useState<ModelConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const reload = () =>
    modelConfig
      .get()
      .then(setConfig)
      .catch((e) =>
        setError(e instanceof ApiRequestError ? e.error.user_message : "加载模型配置失败"),
      );

  useEffect(() => {
    void reload();
  }, []);

  if (error && !config) {
    return (
      <p role="alert" className="rounded-[2px] bg-danger-soft px-3 py-2 text-sm text-danger">
        {error}
      </p>
    );
  }

  if (!config) {
    return (
      <div role="status" aria-label="加载中" className="ff-page max-w-[880px]">
        <span className="sr-only">加载中…</span>
        <div className="ff-ledger">
          <div className="ff-ledger-head">
            <h2>模型</h2>
          </div>
          {[0, 1, 2].map((row) => (
            <div key={row} className="ff-ledger-row">
              <span className="rf-skeleton block h-3 w-1/4" />
              <span className="rf-skeleton ml-auto block h-3 w-1/3" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  const available = config.items.filter((i) => i.available);
  const pending = config.items.filter((i) => !i.available);

  return (
    <div className="ff-page flex max-w-[880px] flex-col gap-3">
      <div className="flex flex-wrap items-baseline gap-x-3">
        <h1 className="text-lg font-semibold tracking-tight text-fg">模型</h1>
        <span className="tnum text-xs text-fg-subtle">
          {available.length} 个能力已接入，{pending.length} 个待接入
        </span>
      </div>
      <p className="max-w-[72ch] text-xs leading-5 text-fg-subtle">
        这里是<strong className="font-medium text-fg-muted">组织默认</strong>：每个能力用哪家上游、
        哪个模型、走谁的账号计费。生成时按 项目设置 › 默认模型 &gt; 这里的组织默认 &gt;
        平台目录默认 的顺序取值；选中的模型出故障时会在<strong className="font-medium text-fg-muted">同一家</strong>
        内换下一个模型，不会静默换到另一家。
      </p>

      {notice && (
        <p role="status" className="rounded-[2px] bg-surface-2 px-3 py-2 text-xs text-fg-muted">
          {notice}
        </p>
      )}

      {available.map((item) => (
        <CapabilitySection
          key={item.capability}
          item={item}
          onChanged={(next, message) => {
            if (next) setConfig(next);
            else void reload();
            setNotice(message);
          }}
        />
      ))}

      {pending.map((item) => (
        <PendingSection key={item.capability} item={item} />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- 已接入的能力

type Draft = { providerId: string; modelId: string; keySource: "platform" | "org" };

/** 目录默认顺序。选它等于 `model_id: null`：这家按优先级自己排。 */
const CATALOG_ORDER = "";

function draftOf(item: CapabilityConfig): Draft {
  const sel = item.selection;
  const providerId = sel?.provider_id ?? item.providers[0]?.provider_id ?? "";
  return { providerId, modelId: sel?.model_id ?? CATALOG_ORDER, keySource: sel?.key_source ?? "platform" };
}

function CapabilitySection({
  item,
  onChanged,
}: {
  item: CapabilityConfig;
  onChanged: (next: ModelConfig | null, message: string) => void;
}) {
  const saved = useMemo(() => draftOf(item), [item]);
  const [draft, setDraft] = useState<Draft>(saved);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** 同时只展开一个 Key 表单，避免两个输入框里都躺着一把明文 */
  const [keyOpen, setKeyOpen] = useState(false);

  useEffect(() => setDraft(saved), [saved]);

  const provider: ProviderOption | undefined = item.providers.find(
    (p) => p.provider_id === draft.providerId,
  );
  const credential = item.credentials.find((c) => c.provider_id === draft.providerId) ?? null;
  const isCustom = provider?.kind === "custom";
  const hasOwnKey = isCustom ? item.custom_endpoint !== null : Boolean(credential?.configured);
  const dirty =
    draft.providerId !== saved.providerId ||
    draft.modelId !== saved.modelId ||
    draft.keySource !== saved.keySource ||
    item.selection?.layer !== "org";

  function pickProvider(next: ProviderOption) {
    setError(null);
    setKeyOpen(false);
    setDraft({
      providerId: next.provider_id,
      modelId: CATALOG_ORDER,
      // 自定义端点只能自有计费；换回目录里的一家时默认回到平台额度
      keySource: next.supports_platform_key ? "platform" : "org",
    });
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const next = await modelConfig.putSelection(item.capability, {
        provider_id: draft.providerId,
        model_id: isCustom || draft.modelId === CATALOG_ORDER ? null : draft.modelId,
        key_source: draft.keySource,
      });
      onChanged(next, `${item.label}的组织默认已保存：${provider?.label ?? draft.providerId}`);
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.error.message || e.error.user_message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  const layerText =
    item.selection?.layer === "org" ? "组织已保存" : "未保存 · 平台目录默认";

  return (
    <section className="ff-ledger" aria-labelledby={`cap-${item.capability}`}>
      <div className="ff-ledger-head">
        <h2 id={`cap-${item.capability}`}>{`${item.label}模型`}</h2>
        <span className="font-normal" data-testid={`layer-${item.capability}`}>
          {layerText}
        </span>
      </div>

      {!item.configurable && (
        <p className="ff-ledger-note">当前 Skill 没有允许改这个能力的上游，只能查看。</p>
      )}

      {/* 上游：同一能力的几家并排，一次选一家 */}
      <div className="ff-ledger-row flex-wrap gap-y-2">
        <span className="ff-ledger-key text-xs text-fg-muted">上游</span>
        <div role="radiogroup" aria-label={`${item.label}上游`} className="ff-ledger-val flex flex-wrap gap-1.5">
          {item.providers.map((p) => {
            const active = p.provider_id === draft.providerId;
            return (
              <button
                key={p.provider_id}
                type="button"
                role="radio"
                aria-checked={active}
                disabled={!item.configurable || !p.available}
                title={p.unavailable_reason ?? undefined}
                onClick={() => pickProvider(p)}
                className={cn(
                  "cursor-pointer rounded-[2px] border px-2.5 py-1 text-xs transition-colors duration-150",
                  "disabled:cursor-not-allowed disabled:border-dashed disabled:opacity-60",
                  active
                    ? "border-primary bg-primary-soft font-medium text-primary"
                    : "border-border-strong bg-surface text-fg-muted hover:text-fg",
                )}
              >
                {p.label}
                {p.kind === "custom" && <span className="ml-1 text-[10px] opacity-80">自定义</span>}
              </button>
            );
          })}
          {item.providers.some((p) => !p.available) && (
            <span className="basis-full text-[11px] leading-5 text-fg-subtle">
              {item.providers.find((p) => !p.available)?.unavailable_reason}（在下方填写后可选）
            </span>
          )}
        </div>
      </div>

      {/* 具体模型 */}
      <div className="ff-ledger-row flex-wrap gap-y-2">
        <label htmlFor={`model-${item.capability}`} className="ff-ledger-key text-xs text-fg-muted">
          模型
        </label>
        <div className="ff-ledger-val min-w-0">
          {isCustom ? (
            <span className="code text-xs text-fg-muted">
              {item.custom_endpoint?.model_id}
              <span className="ml-2 font-sans text-fg-subtle">（随自定义端点，改模型请改端点）</span>
            </span>
          ) : (
            <select
              id={`model-${item.capability}`}
              value={draft.modelId}
              disabled={!item.configurable || busy}
              onChange={(e) => setDraft({ ...draft, modelId: e.target.value })}
              className="h-8 w-full max-w-sm cursor-pointer rounded-md border border-border-strong bg-surface px-2 text-sm text-fg disabled:cursor-not-allowed disabled:opacity-45"
            >
              <option value={CATALOG_ORDER}>
                目录默认顺序{provider?.default_model_id ? `（首选 ${provider.default_model_id}）` : ""}
              </option>
              {(provider?.models ?? []).map((m) => (
                <option key={m.model_id} value={m.model_id}>
                  {m.label}　{m.model_id}
                </option>
              ))}
            </select>
          )}
          {!isCustom && draft.modelId && (
            <span className="mt-1 block text-xs leading-5 text-fg-subtle">
              {provider?.models.find((m) => m.model_id === draft.modelId)?.note}
            </span>
          )}
        </div>
      </div>

      {/* 计费来源 */}
      <div className="ff-ledger-row flex-wrap gap-y-2">
        <span className="ff-ledger-key text-xs text-fg-muted">计费</span>
        <div role="radiogroup" aria-label={`${item.label}计费来源`} className="ff-ledger-val flex flex-wrap items-center gap-x-4 gap-y-1">
          <label className={cn("flex items-center gap-1.5 text-xs", !provider?.supports_platform_key && "opacity-50")}>
            <input
              type="radio"
              name={`bill-${item.capability}`}
              checked={draft.keySource === "platform"}
              disabled={!item.configurable || !provider?.supports_platform_key}
              onChange={() => setDraft({ ...draft, keySource: "platform" })}
            />
            平台额度
          </label>
          <label className={cn("flex items-center gap-1.5 text-xs", !hasOwnKey && "opacity-50")}>
            <input
              type="radio"
              name={`bill-${item.capability}`}
              checked={draft.keySource === "org"}
              disabled={!item.configurable || !hasOwnKey}
              onChange={() => setDraft({ ...draft, keySource: "org" })}
            />
            {isCustom ? "自定义端点的 Key" : `我的 ${provider?.label ?? ""} Key`}
          </label>
          {!hasOwnKey && !isCustom && (
            <span className="text-[11px] text-fg-subtle">先在下方保存这家的 Key，才能选自有计费</span>
          )}
        </div>
      </div>

      {error && (
        <p role="alert" className="mx-4 my-2 rounded-md bg-danger-soft px-2.5 py-1.5 text-xs text-danger">
          {error}
        </p>
      )}

      <div className="ff-ledger-row justify-end gap-2 bg-surface-2">
        <span className="mr-auto text-xs text-fg-subtle">
          {item.selection?.layer === "org"
            ? `已保存：${item.providers.find((p) => p.provider_id === item.selection?.provider_id)?.label ?? item.selection?.provider_id} · ${item.selection?.model_id ?? "目录默认顺序"} · ${item.selection?.key_source === "org" ? "自有 Key" : "平台额度"}`
            : "尚未保存组织默认"}
        </span>
        <Button
          variant="primary"
          size="sm"
          disabled={!item.configurable || busy || !dirty || !provider?.available}
          onClick={() => void save()}
        >
          {busy && <Loader2 aria-hidden className="size-3.5 animate-spin" />}
          保存选择
        </Button>
      </div>

      {/* 这家的 Key（目录里的一家）。自定义端点的 Key 在端点表单里。 */}
      {provider && !isCustom && (
        <div className="ff-ledger-row flex-wrap justify-between gap-y-2">
          <div className="flex min-w-0 flex-col gap-0.5">
            <span className="text-xs font-medium text-fg">{provider.label} 密钥</span>
            <span className="code text-xs text-fg-muted" data-testid={`masked-${item.capability}`}>
              {credential?.configured ? (credential.masked_key ?? "已保存") : "未配置"}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <Button size="sm" onClick={() => setKeyOpen((v) => !v)} aria-expanded={keyOpen}>
              {credential?.configured ? "更换密钥" : "配置密钥"}
            </Button>
            {credential?.configured && (
              <Button
                variant="ghost"
                size="sm"
                onClick={async () => {
                  await providerCredentials.remove(item.capability, provider.provider_id);
                  onChanged(null, `已移除 ${provider.label} 的密钥；选了自有计费的会退回平台额度`);
                }}
              >
                移除
              </Button>
            )}
          </div>
        </div>
      )}
      {provider && !isCustom && keyOpen && (
        <KeyForm
          capability={item.capability}
          providerId={provider.provider_id}
          capabilityLabel={item.label}
          providerLabel={provider.label}
          onSaved={() => {
            setKeyOpen(false);
            onChanged(null, `${provider.label} 的密钥已保存。选择「我的 Key」并保存后才会用它计费`);
          }}
          onCancel={() => setKeyOpen(false)}
        />
      )}

      {item.supports_custom_endpoint && (
        <CustomEndpointForm item={item} onChanged={onChanged} />
      )}
    </section>
  );
}

// ---------------------------------------------------------------- 文本自定义端点

function CustomEndpointForm({
  item,
  onChanged,
}: {
  item: CapabilityConfig;
  onChanged: (next: ModelConfig | null, message: string) => void;
}) {
  const saved = item.custom_endpoint;
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState(saved?.label ?? "");
  const [baseUrl, setBaseUrl] = useState(saved?.base_url ?? "");
  const [modelId, setModelId] = useState(saved?.model_id ?? "");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState<"save" | "test" | "delete" | null>(null);
  const [result, setResult] = useState<KeyTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLabel(saved?.label ?? "");
    setBaseUrl(saved?.base_url ?? "");
    setModelId(saved?.model_id ?? "");
    setApiKey("");
  }, [saved]);

  async function act(kind: "save" | "test" | "delete") {
    setBusy(kind);
    setError(null);
    setResult(null);
    try {
      if (kind === "test") {
        setResult(
          await modelConfig.testEndpoint({
            base_url: baseUrl.trim() || undefined,
            model_id: modelId.trim() || undefined,
            api_key: apiKey.trim() || undefined,
          }),
        );
      } else if (kind === "save") {
        const next = await modelConfig.putEndpoint({
          label: label.trim(),
          base_url: baseUrl.trim(),
          model_id: modelId.trim(),
          ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
        });
        setApiKey("");
        setOpen(false);
        onChanged(next, "自定义端点已保存；在上方「上游」里选中它并保存，文本生成才会改走它");
      } else {
        await modelConfig.deleteEndpoint();
        onChanged(null, "自定义端点已删除；选了它的组织默认已退回平台目录默认");
      }
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.error.message || e.error.user_message : "操作失败");
    } finally {
      setBusy(null);
    }
  }

  const field =
    "h-8 w-full rounded-md border border-border-strong bg-surface px-2.5 text-sm text-fg placeholder:text-fg-subtle";

  return (
    <>
      <div className="ff-ledger-row flex-wrap justify-between gap-y-2 border-t border-border">
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className="text-xs font-medium text-fg">OpenAI 兼容自定义端点</span>
          <span className="truncate text-xs text-fg-subtle" data-testid="custom-endpoint-summary">
            {saved
              ? `${saved.label} · ${saved.base_url} · ${saved.model_id} · ${saved.masked_key ?? "Key 已保存"}`
              : "未配置。只支持文本能力，图片与视频没有通用的兼容协议"}
          </span>
        </div>
        <Button size="sm" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          {saved ? "编辑端点" : "添加端点"}
        </Button>
      </div>

      {open && (
        <div className="grid gap-3 border-t border-border px-4 py-3 sm:grid-cols-2">
          <label className="flex flex-col gap-1 text-xs text-fg">
            供应商名称
            <input className={field} value={label} maxLength={64} onChange={(e) => setLabel(e.target.value)} placeholder="例如：公司内网网关" />
          </label>
          <label className="flex flex-col gap-1 text-xs text-fg">
            模型 ID
            <input className={cn(field, "font-mono")} value={modelId} maxLength={128} spellCheck={false} onChange={(e) => setModelId(e.target.value)} placeholder="例如：qwen2.5-72b-instruct" />
          </label>
          <label className="flex flex-col gap-1 text-xs text-fg sm:col-span-2">
            API 请求地址（Base URL）
            <input className={cn(field, "font-mono")} value={baseUrl} maxLength={512} spellCheck={false} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://your-endpoint.example.com/v1" />
            <span className="text-[11px] leading-5 text-fg-subtle">
              只接受 https；内网、本机与云元数据地址会被拒绝。调用 {"{Base URL}"}/chat/completions。
            </span>
          </label>
          <label className="flex flex-col gap-1 text-xs text-fg sm:col-span-2">
            API Key
            <input
              className={cn(field, "font-mono")}
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={saved ? `已保存 ${saved.masked_key ?? ""}，留空则不更换` : "粘贴 API Key"}
            />
          </label>

          {result && (
            <p role="status" className={cn("flex items-start gap-1.5 rounded-md px-2.5 py-1.5 text-xs sm:col-span-2", result.ok ? "bg-success-soft text-success" : "bg-danger-soft text-danger")}>
              {result.ok ? <Check aria-hidden className="mt-px size-3.5 shrink-0" /> : <X aria-hidden className="mt-px size-3.5 shrink-0" />}
              <span className="min-w-0 break-all">{result.message}</span>
            </p>
          )}
          {error && (
            <p role="alert" className="rounded-md bg-danger-soft px-2.5 py-1.5 text-xs text-danger sm:col-span-2">
              {error}
            </p>
          )}

          <div className="flex flex-wrap items-center gap-2 sm:col-span-2">
            <Button
              variant="primary"
              size="sm"
              disabled={busy !== null || !label.trim() || !baseUrl.trim() || !modelId.trim() || (!saved && !apiKey.trim())}
              onClick={() => void act("save")}
            >
              {busy === "save" && <Loader2 aria-hidden className="size-3.5 animate-spin" />}
              保存端点
            </Button>
            <Button size="sm" disabled={busy !== null || (!saved && (!baseUrl.trim() || !apiKey.trim()))} onClick={() => void act("test")}>
              {busy === "test" && <Loader2 aria-hidden className="size-3.5 animate-spin" />}
              测试连接
            </Button>
            {saved && (
              <Button variant="ghost" size="sm" disabled={busy !== null} onClick={() => void act("delete")}>
                删除端点
              </Button>
            )}
          </div>
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------- 还没接入的能力

function PendingSection({ item }: { item: CapabilityConfig }) {
  return (
    <section className="ff-ledger border-dashed">
      <div className="ff-ledger-head border-dashed">
        <h2 className="text-fg-muted">{`${item.label}模型`}</h2>
        <span className="font-normal">暂未接入</span>
      </div>
      <div className="ff-ledger-row">
        <div className="ff-ledger-val">
          {/* 这句由后端给（`unavailable_reason`），前端不自己判断里程碑 */}
          <p className="text-xs leading-5 text-fg-subtle">{item.unavailable_reason}</p>
          <p className="mt-1.5 text-xs leading-5 text-fg-subtle">
            目录里还没有这个能力的任何 Provider，所以这里
            <strong className="font-medium text-fg-muted">不放选择器也不放密钥入口</strong>
            ——选了不会生效，配了也不会被用到。
          </p>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- Key 表单（某一家）

function KeyForm({
  capability,
  providerId,
  capabilityLabel,
  providerLabel,
  onSaved,
  onCancel,
}: {
  capability: string;
  providerId: string;
  capabilityLabel: string;
  providerLabel: string;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState("");
  const [visible, setVisible] = useState(false);
  const [busy, setBusy] = useState<"test" | "save" | null>(null);
  const [result, setResult] = useState<KeyTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const key = value.trim();

  async function run(kind: "test" | "save") {
    setBusy(kind);
    setError(null);
    try {
      if (kind === "test") setResult(await providerCredentials.test(capability, key, providerId));
      else {
        await providerCredentials.put(capability, key, providerId);
        setValue("");
        onSaved();
      }
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.error.user_message : kind === "test" ? "测试失败" : "保存失败");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-col gap-2 border-t border-border px-4 py-3">
      <label htmlFor={`key-${capability}-${providerId}`} className="text-xs font-medium text-fg">
        {providerLabel} API Key
      </label>
      <div className="relative">
        <input
          id={`key-${capability}-${providerId}`}
          type={visible ? "text" : "password"}
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            setResult(null);
          }}
          autoComplete="off"
          spellCheck={false}
          placeholder="粘贴你的 API Key"
          className="h-9 w-full rounded-md border border-border-strong bg-surface pr-9 pl-2.5 font-mono text-sm text-fg placeholder:font-sans placeholder:text-fg-subtle"
        />
        <button
          type="button"
          onClick={() => setVisible(!visible)}
          aria-label={visible ? "隐藏密钥" : "显示密钥"}
          aria-pressed={visible}
          className="absolute top-1/2 right-1.5 flex size-6 -translate-y-1/2 cursor-pointer items-center justify-center rounded text-fg-subtle hover:bg-surface-2 hover:text-fg"
        >
          {visible ? <EyeOff aria-hidden className="size-3.5" /> : <Eye aria-hidden className="size-3.5" />}
        </button>
      </div>
      <p className="text-xs leading-5 text-fg-subtle">
        密钥加密后保存，服务端不再返回明文。保存 Key 不等于改用它计费——在上方「计费」里选
        「我的 Key」并保存，{capabilityLabel}才会走你自己的账号。
      </p>
      {result && (
        <p role="status" className={cn("flex items-start gap-1.5 rounded-md px-2.5 py-1.5 text-xs", result.ok ? "bg-success-soft text-success" : "bg-danger-soft text-danger")}>
          {result.ok ? <Check aria-hidden className="mt-px size-3.5 shrink-0" /> : <X aria-hidden className="mt-px size-3.5 shrink-0" />}
          <span className="min-w-0 break-all">{result.message}</span>
        </p>
      )}
      {error && (
        <p role="alert" className="rounded-md bg-danger-soft px-2.5 py-1.5 text-xs text-danger">
          {error}
        </p>
      )}
      <div className="flex items-center gap-2">
        <Button variant="primary" size="sm" disabled={!key || busy !== null} onClick={() => void run("save")}>
          {busy === "save" && <Loader2 aria-hidden className="size-3.5 animate-spin" />}
          保存
        </Button>
        <Button size="sm" disabled={!key || busy !== null} onClick={() => void run("test")}>
          {busy === "test" && <Loader2 aria-hidden className="size-3.5 animate-spin" />}
          测试连接
        </Button>
        <Button variant="ghost" size="sm" disabled={busy !== null} onClick={onCancel}>
          取消
        </Button>
      </div>
    </div>
  );
}
