"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, CircleAlert, Eye, EyeOff, KeyRound, Loader2, Plus, Trash2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogCloseButton } from "@/components/ui/dialog";
import {
  ApiRequestError,
  modelConfig,
  providerCredentials,
  type CapabilityConfig,
  type ConnectionReferences,
  type KeyTestResult,
  type ModelConfig,
  type ProtocolSpec,
  type ProviderConnection,
  type ProviderOption,
  type ProviderPreset,
} from "@/lib/api";
import {
  billingChoices,
  brokenReasonText,
  classifyTest,
  createBody,
  currentDefaultText,
  defaultRows,
  describeApiError,
  draftFromConnection,
  draftFromPreset,
  draftProblems,
  draftTestBody,
  patchBody,
  referenceLines,
  savedTestBody,
  sourceText,
  VERDICT_LABEL,
  type ConnectionDraft,
  type DefaultRow,
} from "@/lib/freeflow/provider-scope";
import { cn } from "@/lib/utils";
import { mergeModelOptions } from "@/lib/freeflow/model-options";

import { ConfirmDialog } from "./project/feedback";

/**
 * 05 模型（组织层，ADR-039）。
 *
 * 像 CC Switch 那样用：**添加供应商 → 填 Key → 一键设为默认**。三本账：
 *
 * - **默认模型**：每个已接入能力一本。候选是平台目录里的每一家（模型与计费在行内选）
 *   加上每个供应商连接里这个能力的每个模型（一键）。保存成功才换「当前」，失败保留原值、
 *   显示后端原文。默认指向的连接坏了（`broken_reason`）时顶部醒目提示，下面直接改选。
 * - **供应商**：本组织的连接。直接填写 Key 与地址，读取或手填模型；编辑时 Key 从空开始，
 *   **永不回显**，只显示后端给的尾号掩码。删除前列出谁正指向它——删了不会自动换到别家。
 * - **还没接入的能力**（视频、语音）照实写，不放控件。
 *
 * 选项不在 React 里写死：有哪几家、协议能选哪些、哪个能力能指向连接，全来自后端。
 */
export function ModelCatalogPage() {
  const [config, setConfig] = useState<ModelConfig | null>(null);
  const [connections, setConnections] = useState<ProviderConnection[]>([]);
  const [limit, setLimit] = useState<number | null>(null);
  const [presets, setPresets] = useState<ProviderPreset[]>([]);
  const [protocols, setProtocols] = useState<ProtocolSpec[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [connError, setConnError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const reloadConfig = useCallback(
    () =>
      modelConfig
        .get()
        .then(setConfig)
        .catch((e) => setError(e instanceof ApiRequestError ? e.error.user_message : "加载模型配置失败")),
    [],
  );
  const reloadConnections = useCallback(
    () =>
      modelConfig
        .connections()
        .then((r) => {
          setConnections(r.items);
          setLimit(r.limit);
          setConnError(null);
        })
        .catch((e) => setConnError(e instanceof ApiRequestError ? e.error.user_message : "供应商列表加载失败")),
    [],
  );

  useEffect(() => {
    void reloadConfig();
    void reloadConnections();
    modelConfig
      .presets()
      .then((r) => {
        setPresets(r.presets);
        setProtocols(r.protocols);
      })
      // 协议目录读不到时基础兼容配置仍可用，高级协议选择照实提示
      .catch(() => undefined);
  }, [reloadConfig, reloadConnections]);

  /** 连接改了：默认的聚合视图（候选、broken_reason）也要跟着重取 */
  const afterConnectionChange = useCallback(
    (message: string) => {
      setNotice(message);
      void reloadConnections();
      void reloadConfig();
    },
    [reloadConfig, reloadConnections],
  );

  const capLabel = useCallback(
    (capability: string) =>
      CAP_SHORT[capability] ?? config?.items.find((i) => i.capability === capability)?.label ?? capability,
    [config],
  );

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
          {connections.length} 个供应商 · {available.length} 个能力已接入
        </span>
      </div>
      <p className="max-w-[72ch] text-xs leading-5 text-fg-subtle">
        这里是<strong className="font-medium text-fg-muted">组织默认</strong>，对所有项目生效；单个项目可在
        项目设置 › 默认模型 里覆盖。用自己的供应商时按你的 Key 调用，出错会直接报错，
        <strong className="font-medium text-fg-muted">不会悄悄换到别家或平台</strong>。
      </p>

      {notice && (
        <p role="status" className="flex items-start gap-2 rounded-[2px] bg-surface-2 px-3 py-2 text-xs text-fg-muted">
          <span className="min-w-0 flex-1">{notice}</span>
          <button type="button" aria-label="关闭提示" onClick={() => setNotice(null)} className="cursor-pointer text-fg-subtle hover:text-fg">
            <X aria-hidden className="size-3.5" />
          </button>
        </p>
      )}

      <ConnectionsSection
        connections={connections}
        limit={limit}
        presets={presets}
        protocols={protocols}
        loadError={connError}
        capLabel={capLabel}
        onChanged={afterConnectionChange}
      />

      {available.map((item) => (
        <DefaultSection
          key={item.capability}
          item={item}
          connections={connections}
          onSaved={(next, message) => {
            setConfig(next);
            setNotice(message);
          }}
          onKeyChanged={(message) => {
            setNotice(message);
            void reloadConfig();
          }}
        />
      ))}

      {pending.map((item) => (
        <PendingSection key={item.capability} item={item} />
      ))}
    </div>
  );
}

/** 能力的短名：账本里「文本 / 出图」比「文本生成 / 图片生成」更好扫 */
const CAP_SHORT: Record<string, string> = { text_generation: "文本", image_generation: "出图" };

function errorText(e: unknown, fallback: string): string {
  if (!(e instanceof ApiRequestError)) return fallback;
  const fields = (e.error.detail?.errors ?? []).map((x) => x.message).filter(Boolean);
  const base = describeApiError(e.error, true);
  return fields.length && !fields.every((f) => base.includes(f)) ? `${base}：${fields.join("；")}` : base;
}

// ---------------------------------------------------------------- 默认模型（一键切换）

function DefaultSection({
  item,
  connections,
  onSaved,
  onKeyChanged,
}: {
  item: CapabilityConfig;
  connections: ProviderConnection[];
  onSaved: (next: ModelConfig, message: string) => void;
  onKeyChanged: (message: string) => void;
}) {
  const rows = useMemo(() => defaultRows(item, connections), [item, connections]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const broken = brokenReasonText(item.selection?.broken_reason);
  const catalogRows = rows.filter((r) => r.kind === "catalog");
  const orgRows = rows.filter((r): r is Extract<DefaultRow, { kind: "org" }> => r.kind === "org");

  async function put(key: string, body: Parameters<typeof modelConfig.putSelection>[1], label: string) {
    setBusy(key);
    setError(null);
    try {
      const next = await modelConfig.putSelection(item.capability, body);
      onSaved(next, `${item.label}的组织默认已改为 ${label}`);
    } catch (e) {
      // 失败不动「当前」：显示的仍是服务端那份
      setError(errorText(e, "保存失败"));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="ff-ledger" aria-labelledby={`cap-${item.capability}`}>
      <div className="ff-ledger-head flex-wrap">
        <h2 id={`cap-${item.capability}`}>{`默认${item.label}模型`}</h2>
        <span className="min-w-0 font-normal" data-testid={`current-${item.capability}`}>
          当前：{currentDefaultText(item)}
        </span>
      </div>

      {broken && (
        <div className="ff-ledger-row bg-danger-soft" data-flag="danger" role="alert" data-testid={`broken-${item.capability}`}>
          <CircleAlert aria-hidden className="size-4 shrink-0 text-danger" />
          <p className="min-w-0 text-xs leading-5 text-danger">
            <strong className="font-semibold">{broken}。</strong>
            现在生成{item.label}会报错，不会自动换到别家。在下面另选一个设为默认。
          </p>
        </div>
      )}

      {!item.configurable && (
        <p className="ff-ledger-note">当前 Skill 没有允许改这个能力的上游，只能查看。</p>
      )}

      {catalogRows.map((r) => {
        const provider = item.providers.find((p) => p.provider_id === r.providerId);
        return provider ? (
          <CatalogRow
            key={r.key}
            item={item}
            provider={provider}
            current={r.current}
            busy={busy}
            onSave={(body, label) => void put(r.key, body, label)}
            onKeyChanged={onKeyChanged}
          />
        ) : null;
      })}

      {item.supports_org_connections && (
        <>
          {/* 分段小标题不用 `.ff-ledger-row`：studio.css 不在 Tailwind 的 layer 里，它的
              `align-items: center` 会压过 `items-start`，竖排之后标题被挤到正中 */}
          <div className="flex flex-col items-start gap-0.5 border-b border-border px-4 py-2 text-left">
            <span className="text-[11px] font-medium text-fg-subtle">我的供应商</span>
            <span className="text-xs leading-5 text-fg-subtle">
              你在上方「供应商」里添加的连接在这里选：点「设为默认」后{item.label}就按这个连接的 Key 调用。
              和上面平台行里的「官方 Key」是两回事，不用另外配置。
            </span>
          </div>
          {orgRows.length === 0 && (
            <p className="ff-ledger-empty px-4 py-3 text-xs text-fg-subtle">
              还没有能做{item.label}的供应商。在上方「供应商」里添加后，这里可以一键切换。
            </p>
          )}
          {orgRows.map((r) => (
            <div
              key={r.key}
              className="ff-ledger-row flex-wrap gap-y-1.5"
              data-flag={r.current ? "review" : undefined}
              data-testid={`default-row-${r.connectionId}-${r.modelId}`}
            >
              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="truncate text-sm text-fg">{r.label}</span>
                <span className="flex flex-wrap items-center gap-1.5 text-xs text-fg-muted">
                  <span className="code">{r.modelId}</span>
                  {r.unverified && <UnverifiedTag />}
                  {r.reasoning && <ReasoningTag />}
                  {!r.available && <span className="text-fg-subtle">{r.unavailableReason ?? "不可用"}</span>}
                </span>
              </div>
              {r.current ? (
                <CurrentBadge />
              ) : (
                <Button
                  size="sm"
                  disabled={!item.configurable || !r.available || busy !== null}
                  title={!r.available ? (r.unavailableReason ?? undefined) : undefined}
                  aria-label={`把 ${r.label} · ${r.modelId} 设为${item.label}默认`}
                  onClick={() =>
                    void put(r.key, { provider_id: r.providerId, model_id: r.modelId, key_source: "org" }, `${r.label} · ${r.modelId}`)
                  }
                >
                  {busy === r.key && <Loader2 aria-hidden className="size-3.5 animate-spin" />}
                  设为默认
                </Button>
              )}
            </div>
          ))}
        </>
      )}

      {error && (
        <p role="alert" className="mx-4 my-2 rounded-md bg-danger-soft px-2.5 py-1.5 text-xs text-danger" data-testid={`default-error-${item.capability}`}>
          {error}
        </p>
      )}
    </section>
  );
}

/** 目录默认顺序。选它等于 `model_id: null`：这家按优先级自己排 */
const CATALOG_ORDER = "";

/** 平台目录里的一家：模型与计费在行内选，再点「设为默认」 */
function CatalogRow({
  item,
  provider,
  current,
  busy,
  onSave,
  onKeyChanged,
}: {
  item: CapabilityConfig;
  provider: ProviderOption;
  current: boolean;
  busy: string | null;
  onSave: (body: Parameters<typeof modelConfig.putSelection>[1], label: string) => void;
  onKeyChanged: (message: string) => void;
}) {
  const sel = item.selection;
  const savedModel = current ? (sel?.model_id ?? CATALOG_ORDER) : CATALOG_ORDER;
  const savedSource = current ? (sel?.key_source ?? "platform") : "platform";
  const [modelId, setModelId] = useState(savedModel);
  const [keySource, setKeySource] = useState<"platform" | "org">(savedSource);
  const [keyOpen, setKeyOpen] = useState(false);
  const credential = item.credentials.find((c) => c.provider_id === provider.provider_id) ?? null;
  const hasOwnKey = Boolean(credential?.configured);
  const billing = billingChoices(item, provider, hasOwnKey);

  useEffect(() => {
    setModelId(savedModel);
    setKeySource(savedSource);
  }, [savedModel, savedSource]);

  const dirty = modelId !== savedModel || keySource !== savedSource;
  const id = `model-${item.capability}-${provider.provider_id}`;

  return (
    <>
      <div className="ff-ledger-row flex-wrap gap-y-2" data-form="true" data-flag={current ? "review" : undefined}>
        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          <span className="text-sm text-fg">
            平台 · {provider.label}
            {!provider.available && <span className="ml-2 text-xs text-fg-subtle">{provider.unavailable_reason}</span>}
          </span>
          <div className="flex flex-wrap items-center gap-2">
            <label htmlFor={id} className="sr-only">
              {provider.label} 的模型
            </label>
            <select
              id={id}
              value={modelId}
              disabled={!item.configurable || busy !== null}
              onChange={(e) => setModelId(e.target.value)}
              className="h-8 w-full min-w-0 cursor-pointer rounded-md border border-border-strong bg-surface px-2 text-sm text-fg disabled:cursor-not-allowed disabled:opacity-45 sm:w-auto sm:max-w-xs sm:flex-1"
            >
              <option value={CATALOG_ORDER}>
                目录默认顺序{provider.default_model_id ? `（首选 ${provider.default_model_id}）` : ""}
              </option>
              {provider.models.map((m) => (
                <option key={m.model_id} value={m.model_id}>
                  {m.label}　{m.model_id}
                </option>
              ))}
            </select>
            <div role="radiogroup" aria-label={`${provider.label} 计费来源`} className="flex flex-wrap items-center gap-x-3 gap-y-1">
              {(["platform", "own"] as const).map((which) => {
                const choice = billing[which];
                const value = which === "platform" ? "platform" : "org";
                return (
                  <label
                    key={which}
                    className={cn("flex items-center gap-1.5 text-xs", choice.disabled ? "cursor-not-allowed text-fg-subtle" : "cursor-pointer")}
                    title={choice.reason ?? undefined}
                  >
                    <input
                      type="radio"
                      name={`bill-${item.capability}-${provider.provider_id}`}
                      checked={keySource === value}
                      disabled={choice.disabled}
                      aria-describedby={item.configurable && choice.reason ? `${id}-${which}-why` : undefined}
                      onChange={() => setKeySource(value)}
                    />
                    {choice.label}
                  </label>
                );
              })}
            </div>
          </div>
          {/* Skill 锁住时整段上方已有一句说明，这里只写各自的原因 */}
          {item.configurable && (billing.platform.reason || billing.own.reason) && (
            <p className="flex flex-col text-xs leading-5 text-fg-subtle">
              {billing.platform.reason && <span id={`${id}-platform-why`}>{billing.platform.reason}。</span>}
              {billing.own.reason && <span id={`${id}-own-why`}>{billing.own.reason}。</span>}
            </p>
          )}
          {modelId && (
            <span className="text-xs leading-5 text-fg-subtle">
              {provider.models.find((m) => m.model_id === modelId)?.note}
            </span>
          )}
          <span className="flex flex-wrap items-center gap-2 text-xs text-fg-subtle">
            <span>
              {provider.label} 官方 Key：
              <span className="code" data-testid={`masked-${item.capability}`}>
                {hasOwnKey ? (credential?.masked_key ?? "已保存") : "未配置"}
              </span>
            </span>
            <Button
              size="sm"
              variant={hasOwnKey ? "ghost" : undefined}
              aria-expanded={keyOpen}
              onClick={() => setKeyOpen((v) => !v)}
            >
              <KeyRound aria-hidden className="size-3.5" />
              {hasOwnKey ? `更换 ${provider.label} Key` : `配置 ${provider.label} Key`}
            </Button>
            {hasOwnKey && (
              <button
                type="button"
                className="cursor-pointer text-fg-muted underline-offset-2 hover:underline"
                onClick={async () => {
                  await providerCredentials.remove(item.capability, provider.provider_id);
                  onKeyChanged(`已移除 ${provider.label} 的密钥；选了自有计费的会退回平台额度`);
                }}
              >
                移除
              </button>
            )}
          </span>
        </div>
        {current && !dirty ? (
          <CurrentBadge />
        ) : (
          <Button
            size="sm"
            disabled={!item.configurable || !provider.available || busy !== null}
            aria-label={`把 平台 · ${provider.label} 设为${item.label}默认`}
            onClick={() =>
              onSave(
                { provider_id: provider.provider_id, model_id: modelId === CATALOG_ORDER ? null : modelId, key_source: keySource },
                `平台 · ${provider.label}`,
              )
            }
          >
            {busy === provider.provider_id && <Loader2 aria-hidden className="size-3.5 animate-spin" />}
            {current ? "保存修改" : "设为默认"}
          </Button>
        )}
      </div>
      {keyOpen && (
        <KeyForm
          capability={item.capability}
          providerId={provider.provider_id}
          capabilityLabel={item.label}
          providerLabel={provider.label}
          onSaved={() => {
            setKeyOpen(false);
            onKeyChanged(`${provider.label} 官方 Key 已保存。选「我的 ${provider.label} 官方 Key」并设为默认后才会用它计费`);
          }}
          onCancel={() => setKeyOpen(false)}
        />
      )}
    </>
  );
}

/** 已选中的那一行。比一行灰字醒目：用户要一眼看出「现在用的是哪一家」 */
function CurrentBadge() {
  return (
    <span className="inline-flex items-center gap-1 rounded-[2px] bg-primary-soft px-2 py-1 text-xs font-medium text-primary">
      <Check aria-hidden className="size-3.5" />
      已选为默认
    </span>
  );
}

function UnverifiedTag() {
  return (
    <span
      className="rounded-[2px] bg-running-soft px-1.5 py-px text-[11px] text-running"
      title="第三方出图还没做画风一致性实测，角色与场景可能出现漂移"
    >
      画风一致性未实测
    </span>
  );
}

function ReasoningTag() {
  return <span className="rounded-[2px] bg-surface-2 px-1.5 py-px text-[11px] text-fg-muted">推理模型</span>;
}

// ---------------------------------------------------------------- 供应商（连接）

function ConnectionsSection({
  connections,
  limit,
  presets,
  protocols,
  loadError,
  capLabel,
  onChanged,
}: {
  connections: ProviderConnection[];
  limit: number | null;
  presets: ProviderPreset[];
  protocols: ProtocolSpec[];
  loadError: string | null;
  capLabel: (capability: string) => string;
  onChanged: (message: string) => void;
}) {
  const [drawer, setDrawer] = useState<{ mode: "create" } | { mode: "edit"; conn: ProviderConnection } | null>(null);
  const [removing, setRemoving] = useState<ProviderConnection | null>(null);
  const full = limit !== null && connections.length >= limit;

  return (
    <section className="ff-ledger" aria-labelledby="providers-head">
      <div className="ff-ledger-head">
        <h2 id="providers-head">供应商</h2>
        <span className="flex items-center gap-2 font-normal">
          <span className="tnum">
            {connections.length}
            {limit !== null ? ` / ${limit}` : ""}
          </span>
          <Button
            size="sm"
            variant="primary"
            disabled={full}
            title={full ? "已达上限，先删掉不用的" : undefined}
            onClick={() => setDrawer({ mode: "create" })}
          >
            <Plus aria-hidden className="size-3.5" />
            添加供应商
          </Button>
        </span>
      </div>
      <p className="ff-ledger-note">
        用你自己的 Key 接入其他厂商的文本或出图模型。Key 加密保存，之后只显示尾号。
      </p>

      {loadError && (
        <p role="alert" className="mx-4 my-2 rounded-md bg-danger-soft px-2.5 py-1.5 text-xs text-danger">
          {loadError}
        </p>
      )}
      {!loadError && connections.length === 0 && (
        <p className="ff-ledger-empty px-4 py-3 text-xs text-fg-subtle">还没有添加供应商。</p>
      )}

      {connections.map((c) => (
        <ConnectionRow
          key={c.id}
          conn={c}
          presets={presets}
          capLabel={capLabel}
          onEdit={() => setDrawer({ mode: "edit", conn: c })}
          onRemove={() => setRemoving(c)}
          onChanged={onChanged}
        />
      ))}

      {drawer && (
        <ConnectionDrawer
          key={drawer.mode === "edit" ? drawer.conn.id : "create"}
          mode={drawer.mode}
          saved={drawer.mode === "edit" ? drawer.conn : null}
          presets={presets}
          protocols={protocols}
          capLabel={capLabel}
          onClose={() => setDrawer(null)}
          onSaved={(message) => {
            setDrawer(null);
            onChanged(message);
          }}
        />
      )}

      {removing && (
        <DeleteConnectionDialog
          conn={removing}
          capLabel={capLabel}
          onClose={() => setRemoving(null)}
          onDeleted={(message) => {
            setRemoving(null);
            onChanged(message);
          }}
        />
      )}
    </section>
  );
}

function ConnectionRow({
  conn,
  presets,
  capLabel,
  onEdit,
  onRemove,
  onChanged,
}: {
  conn: ProviderConnection;
  presets: ProviderPreset[];
  capLabel: (capability: string) => string;
  onEdit: () => void;
  onRemove: () => void;
  onChanged: (message: string) => void;
}) {
  const [busy, setBusy] = useState<"toggle" | "test" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<KeyTestResult | null>(null);

  async function toggle() {
    setBusy("toggle");
    setError(null);
    try {
      await modelConfig.updateConnection(conn.id, { enabled: !conn.enabled });
      onChanged(
        conn.enabled
          ? `已停用 ${conn.label}；指向它的默认与项目设置会报错，不会自动换到别家`
          : `已启用 ${conn.label}`,
      );
    } catch (e) {
      setError(errorText(e, "操作失败"));
    } finally {
      setBusy(null);
    }
  }

  async function test() {
    setBusy("test");
    setError(null);
    setResult(null);
    try {
      setResult(await modelConfig.testSaved(conn.id, {}));
    } catch (e) {
      setError(errorText(e, "测试失败"));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="ff-ledger-row" data-flag={conn.enabled ? undefined : "attention"} data-testid={`conn-${conn.id}`}>
      {/* `.ff-ledger-row` 是不分层的 align-items:center，压得过 Tailwind 的 items-stretch，所以内容另包一层撑满 */}
      <div className="flex w-full min-w-0 flex-col gap-2">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="text-sm font-medium text-fg">{conn.label}</span>
          <span className="text-xs text-fg-subtle">{sourceText(conn, presets)}</span>
          <span
            className={cn(
              "rounded-[2px] px-1.5 py-px text-[11px]",
              conn.enabled ? "bg-success-soft text-success" : "bg-surface-2 text-fg-muted",
            )}
          >
            {conn.enabled ? "启用" : "已停用"}
          </span>
        </div>
        <dl className="grid min-w-0 grid-cols-[72px_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
          <dt className="text-fg-subtle">Base URL</dt>
          <dd className="code min-w-0 truncate text-fg-muted" title={conn.base_url}>
            {conn.base_url}
          </dd>
          <dt className="text-fg-subtle">Key</dt>
          <dd className="code text-fg-muted" data-testid={`conn-key-${conn.id}`}>
            {conn.masked_key ?? "已保存（无法读取尾号，建议重新填写）"}
          </dd>
          <dt className="text-fg-subtle">模型</dt>
          <dd className="flex min-w-0 flex-col gap-1">
            {conn.models.map((m) => (
              <span key={`${m.protocol}|${m.model_id}`} className="flex min-w-0 flex-wrap items-center gap-1.5">
                <span className="code min-w-0 truncate text-fg">{m.model_id}</span>
                <span className="text-fg-subtle">{capLabel(m.capability)}</span>
                {m.consistency_verified === false && <UnverifiedTag />}
                {m.reasoning && <ReasoningTag />}
              </span>
            ))}
          </dd>
        </dl>
        {result && <TestResultLine result={result} />}
        {error && (
          <p role="alert" className="rounded-md bg-danger-soft px-2.5 py-1.5 text-xs text-danger">
            {error}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" disabled={busy !== null || !conn.enabled} title={conn.enabled ? undefined : "先启用再测试"} onClick={() => void test()}>
            {busy === "test" && <Loader2 aria-hidden className="size-3.5 animate-spin" />}
            测试连接
          </Button>
          <Button size="sm" disabled={busy !== null} onClick={onEdit} aria-label={`编辑 ${conn.label}`}>
            编辑
          </Button>
          <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => void toggle()}>
            {busy === "toggle" && <Loader2 aria-hidden className="size-3.5 animate-spin" />}
            {conn.enabled ? "停用" : "启用"}
          </Button>
          <Button size="sm" variant="ghost" disabled={busy !== null} onClick={onRemove} aria-label={`删除 ${conn.label}`}>
            <Trash2 aria-hidden className="size-3.5" />
            删除
          </Button>
        </div>
      </div>
    </div>
  );
}

function TestResultLine({ result }: { result: KeyTestResult }) {
  const verdict = classifyTest(result);
  return (
    <p
      role="status"
      data-verdict={verdict}
      className={cn(
        "flex items-start gap-1.5 rounded-md px-2.5 py-1.5 text-xs",
        verdict === "pass" && "bg-success-soft text-success",
        verdict === "neutral" && "bg-running-soft text-running",
        verdict === "fail" && "bg-danger-soft text-danger",
      )}
    >
      {verdict === "pass" ? (
        <Check aria-hidden className="mt-px size-3.5 shrink-0" />
      ) : verdict === "neutral" ? (
        <CircleAlert aria-hidden className="mt-px size-3.5 shrink-0" />
      ) : (
        <X aria-hidden className="mt-px size-3.5 shrink-0" />
      )}
      <span className="min-w-0 break-all">
        <strong className="font-semibold">{VERDICT_LABEL[verdict]}：</strong>
        {result.message}
      </span>
    </p>
  );
}

// ---------------------------------------------------------------- 添加 / 编辑抽屉

function ConnectionDrawer({
  mode,
  saved,
  presets,
  protocols,
  capLabel,
  onClose,
  onSaved,
}: {
  mode: "create" | "edit";
  saved: ProviderConnection | null;
  presets: ProviderPreset[];
  protocols: ProtocolSpec[];
  capLabel: (capability: string) => string;
  onClose: () => void;
  onSaved: (message: string) => void;
}) {
  /** 新建直接填写 Key + 地址；厂商与模型不固定。 */
  const [draft, setDraft] = useState<ConnectionDraft | null>(
    saved ? draftFromConnection(saved) : draftFromPreset(null, protocols),
  );
  const [discovered, setDiscovered] = useState<string[]>([]);
  const [discoveryNote, setDiscoveryNote] = useState<string | null>(null);
  const [busy, setBusy] = useState<"save" | "test" | "discover" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<KeyTestResult | null>(null);
  const [showKey, setShowKey] = useState(false);
  const preset = draft?.presetId
    ? (presets.find((p) => p.preset_id === draft.presetId) ?? null)
    : null;

  function patch(next: Partial<ConnectionDraft>) {
    setDraft((d) => (d ? { ...d, ...next } : d));
    setResult(null);
    if (next.baseUrl !== undefined || next.apiKey !== undefined) {
      setDiscovered([]);
      setDiscoveryNote(null);
    }
  }

  async function discover() {
    if (!draft) return;
    setBusy("discover");
    setError(null);
    setDiscoveryNote(null);
    try {
      const response = await modelConfig.discoverModels(
        {
          base_url: draft.baseUrl.trim(),
          ...(draft.apiKey.trim() ? { api_key: draft.apiKey.trim() } : {}),
        },
        saved?.id,
      );
      setDiscovered(response.models);
      setDraft((d) => (d ? { ...d, baseUrl: response.base_url } : d));
      setDiscoveryNote(
        response.mock
          ? "测试环境模型列表（Mock），未连接真实上游"
          : `读取到 ${response.models.length} 个模型，请选择要使用的模型。列表只说明 Key 能读取模型；平台用 OpenAI Chat Completions 生成文本、OpenAI Images 出图，所选模型是否支持以第一次生成为准`,
      );
    } catch (e) {
      setError(errorText(e, "读取模型失败，可以在高级设置中手动填写模型 ID"));
    } finally {
      setBusy(null);
    }
  }

  const problems = draft ? draftProblems(draft, mode) : [];
  const testBody = draft
    ? saved
      ? savedTestBody(draft, saved)
      : draftTestBody(draft)
    : null;
  const changes = draft && saved ? patchBody(draft, saved) : null;
  const nothingChanged = changes !== null && Object.keys(changes).length === 0;

  async function save() {
    if (!draft) return;
    setBusy("save");
    setError(null);
    try {
      if (saved) {
        await modelConfig.updateConnection(saved.id, patchBody(draft, saved));
        onSaved(`${draft.label.trim()} 已保存`);
      } else {
        const created = await modelConfig.createConnection(createBody(draft));
        onSaved(
          `已添加 ${created.label}。在默认模型区域点「设为默认」就会改用它`,
        );
      }
      setDraft((d) => (d ? { ...d, apiKey: "" } : d));
    } catch (e) {
      // 抽屉不关、草稿不清：用户改一处再存就行
      setError(errorText(e, "保存失败"));
    } finally {
      setBusy(null);
    }
  }

  async function test() {
    if (!draft) return;
    setBusy("test");
    setError(null);
    setResult(null);
    try {
      if (saved)
        setResult(
          await modelConfig.testSaved(saved.id, savedTestBody(draft, saved)),
        );
      else {
        const body = draftTestBody(draft);
        if (body) setResult(await modelConfig.testDraft(body));
      }
    } catch (e) {
      setError(errorText(e, "测试失败"));
    } finally {
      setBusy(null);
    }
  }

  const field =
    "h-8 w-full min-w-0 rounded-md border border-border-strong bg-surface px-2.5 text-sm text-fg placeholder:text-fg-subtle";
  const title = saved
    ? `编辑 ${saved.label}`
    : draft
      ? preset
        ? `添加 ${preset.label}`
        : "添加自定义供应商"
      : "添加供应商";

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      placement="right"
      dismissible={busy === null}
      labelledBy="conn-drawer-title"
      className="h-full w-full max-w-[560px] overflow-y-auto border-l border-border bg-surface shadow-lg"
    >
      <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
        <h2 id="conn-drawer-title" className="text-sm font-semibold text-fg">
          {title}
        </h2>
        <DialogCloseButton disabled={busy !== null} onClick={onClose} />
      </div>

      {draft && (
        <div className="flex flex-col gap-3 p-4">
          {preset && (
            <p className="text-xs text-fg-subtle">
              {preset.key_url ? (
                <a
                  href={preset.key_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-primary hover:underline"
                >
                  去 {preset.label} 取 Key
                </a>
              ) : (
                <a
                  href={preset.docs_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-primary hover:underline"
                >
                  {preset.label} 文档（取 Key 方法见文档）
                </a>
              )}
            </p>
          )}
          <p className="text-sm leading-6 text-fg-muted">
            填写 API Key 和请求地址，读取模型后选择使用。无需先选厂商。供应商需兼容 OpenAI
            接口：文本走 Chat Completions，出图走 Images；只提供 Responses 等其他接口的模型暂不支持。
          </p>
          <label className="flex flex-col gap-1 text-xs text-fg">
            API 请求地址
            <input
              className={cn(field, "font-mono")}
              value={draft.baseUrl}
              disabled={busy !== null}
              maxLength={512}
              spellCheck={false}
              onChange={(e) => patch({ baseUrl: e.target.value })}
              placeholder="https://api.example.com/v1"
            />
            <span className="text-[11px] leading-5 text-fg-subtle">
              填写兼容 API 的基础地址或完整请求地址，例如
              https://api.example.com/v1/chat/completions。
            </span>
          </label>
          <label className="flex flex-col gap-1 text-xs text-fg">
            API Key
            <span className="relative">
              <input
                className={cn(field, "pr-9 font-mono")}
                type={showKey ? "text" : "password"}
                autoComplete="off"
                spellCheck={false}
                value={draft.apiKey}
                disabled={busy !== null}
                onChange={(e) => patch({ apiKey: e.target.value })}
                placeholder={
                  saved
                    ? `已保存 ${saved.masked_key ?? ""}，留空不更换`
                    : "粘贴 API Key"
                }
                data-testid="conn-key-input"
              />
              <button
                type="button"
                onClick={() => setShowKey((v) => !v)}
                aria-label={showKey ? "隐藏密钥" : "显示密钥"}
                aria-pressed={showKey}
                className="absolute top-1/2 right-1.5 flex size-6 -translate-y-1/2 cursor-pointer items-center justify-center rounded text-fg-subtle hover:bg-surface-2 hover:text-fg"
              >
                {showKey ? (
                  <EyeOff aria-hidden className="size-3.5" />
                ) : (
                  <Eye aria-hidden className="size-3.5" />
                )}
              </button>
            </span>
          </label>

          <Button
            size="sm"
            className="self-start"
            disabled={
              busy !== null ||
              !draft.baseUrl.trim() ||
              (!saved && draft.apiKey.trim().length < 8)
            }
            onClick={() => void discover()}
          >
            {busy === "discover" && (
              <Loader2 aria-hidden className="size-3.5 animate-spin" />
            )}
            读取可用模型
          </Button>
          {discoveryNote && (
            <p role="status" className="text-xs text-fg-muted">
              {discoveryNote}
            </p>
          )}
          <label className="flex flex-col gap-1 text-xs text-fg">
            使用模型
            <select
              className={field}
              value={draft.models[0]?.model_id ?? ""}
              disabled={busy !== null}
              onChange={(e) =>
                patch({
                  models: [
                    {
                      model_id: e.target.value,
                      protocol: draft.models[0]?.protocol ?? "openai_chat",
                      reasoning: draft.models[0]?.reasoning ?? false,
                    },
                    ...draft.models.slice(1),
                  ],
                })
              }
            >
              <option value="">
                {discovered.length
                  ? "请选择模型"
                  : "先读取模型，或在高级设置中手动填写"}
              </option>
              {mergeModelOptions(
                discovered,
                draft.models.map((m) => m.model_id),
              ).map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-fg">
            用途
            <select
              className={field}
              value={draft.models[0]?.protocol ?? "openai_chat"}
              disabled={busy !== null}
              onChange={(e) =>
                patch({
                  models: draft.models.map((m, i) =>
                    i === 0
                      ? { ...m, protocol: e.target.value, reasoning: false }
                      : m,
                  ),
                })
              }
            >
              <option value="openai_chat">文本生成</option>
              <option value="openai_images">图片生成</option>
              {draft.models[0] &&
                !["openai_chat", "openai_images"].includes(
                  draft.models[0].protocol,
                ) && (
                  <option value={draft.models[0].protocol}>
                    {draft.models[0].protocol}
                  </option>
                )}
            </select>
            <span className="text-xs text-fg-subtle">
              模型列表不提供可靠的能力信息，请按所选模型的实际用途选择。
            </span>
          </label>
          <details className="border-t border-border pt-3">
            <summary className="cursor-pointer text-sm text-fg-muted">
              高级设置：名称、手填模型、多模型与协议
            </summary>
            <div className="mt-3 flex flex-col gap-3">
              <label className="flex flex-col gap-1 text-xs text-fg">
                名称
                <input
                  className={field}
                  value={draft.label}
                  maxLength={64}
                  onChange={(e) => patch({ label: e.target.value })}
                  placeholder="例如：公司网关"
                />
              </label>
              <fieldset
                disabled={busy !== null}
                className="flex flex-col gap-2"
              >
                <legend className="mb-1 text-xs text-fg">模型</legend>
                {protocols.length === 0 && (
                  <p className="text-xs text-danger">
                    协议目录暂不可用，仍可手填兼容协议的模型；其他协议请稍后再试。
                  </p>
                )}
                {draft.models.map((m, i) => {
                  const spec = protocols.find((p) => p.protocol === m.protocol);
                  const isText = spec?.capability === "text_generation";
                  const set = (next: Partial<typeof m>) =>
                    patch({
                      models: draft.models.map((x, j) =>
                        j === i ? { ...x, ...next } : x,
                      ),
                    });
                  return (
                    <div
                      key={i}
                      className="flex flex-col gap-1.5 rounded-[2px] border border-border p-2"
                      data-testid={`model-row-${i}`}
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <input
                          aria-label={`模型 ${i + 1} 的 ID`}
                          className={cn(
                            field,
                            "min-w-[160px] flex-1 font-mono",
                          )}
                          value={m.model_id}
                          maxLength={128}
                          spellCheck={false}
                          onChange={(e) => set({ model_id: e.target.value })}
                          placeholder="模型 ID"
                        />
                        <select
                          aria-label={`模型 ${i + 1} 的协议`}
                          value={m.protocol}
                          onChange={(e) =>
                            set({ protocol: e.target.value, reasoning: false })
                          }
                          className="h-8 cursor-pointer rounded-md border border-border-strong bg-surface px-2 text-sm text-fg"
                        >
                          {!spec && (
                            <option value={m.protocol}>
                              {m.protocol || "选协议"}
                            </option>
                          )}
                          {protocols.map((p) => (
                            <option key={p.protocol} value={p.protocol}>
                              {p.label}（{capLabel(p.capability)}）
                            </option>
                          ))}
                        </select>
                        <button
                          type="button"
                          aria-label={`移除模型 ${i + 1}`}
                          disabled={draft.models.length <= 1}
                          onClick={() =>
                            patch({
                              models: draft.models.filter((_, j) => j !== i),
                            })
                          }
                          className="grid size-7 cursor-pointer place-items-center rounded text-fg-subtle hover:bg-surface-2 hover:text-fg disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          <X aria-hidden className="size-3.5" />
                        </button>
                      </div>
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                        {isText && (
                          <label className="flex items-center gap-1.5 text-fg">
                            <input
                              type="checkbox"
                              checked={m.reasoning}
                              onChange={(e) =>
                                set({ reasoning: e.target.checked })
                              }
                            />
                            推理模型
                          </label>
                        )}
                        {isText && (
                          <span className="text-[11px] text-fg-subtle">
                            流程中的分类、结构化环节不能用推理模型
                          </span>
                        )}
                        {spec?.consistency_verified === false && (
                          <UnverifiedTag />
                        )}
                      </div>
                    </div>
                  );
                })}
                <Button
                  size="sm"
                  variant="ghost"
                  className="self-start"
                  disabled={draft.models.length >= 50 || protocols.length === 0}
                  onClick={() =>
                    patch({
                      models: [
                        ...draft.models,
                        {
                          model_id: "",
                          protocol:
                            draft.models.at(-1)?.protocol ??
                            protocols[0]?.protocol ??
                            "",
                          reasoning: false,
                        },
                      ],
                    })
                  }
                >
                  <Plus aria-hidden className="size-3.5" />
                  加一个模型
                </Button>
              </fieldset>
            </div>
          </details>

          {result && <TestResultLine result={result} />}
          {error && (
            <p
              role="alert"
              className="rounded-md bg-danger-soft px-2.5 py-1.5 text-xs text-danger"
              data-testid="conn-save-error"
            >
              {error}
            </p>
          )}
          {problems.length > 0 && (
            <p className="text-[11px] leading-5 text-fg-subtle">
              还差：{problems.join("、")}
            </p>
          )}

          <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
            <Button
              variant="primary"
              size="sm"
              disabled={busy !== null || problems.length > 0 || nothingChanged}
              title={nothingChanged ? "没有改动" : undefined}
              onClick={() => void save()}
            >
              {busy === "save" && (
                <Loader2 aria-hidden className="size-3.5 animate-spin" />
              )}
              保存
            </Button>
            <Button
              size="sm"
              disabled={busy !== null || !testBody}
              onClick={() => void test()}
            >
              {busy === "test" && (
                <Loader2 aria-hidden className="size-3.5 animate-spin" />
              )}
              测试连接
            </Button>
            <span className="text-[11px] text-fg-subtle">
              测试只发一次不花钱的请求，测第一个模型。
            </span>
          </div>
        </div>
      )}
    </Dialog>
  );
}

// ---------------------------------------------------------------- 删除确认

function DeleteConnectionDialog({
  conn,
  capLabel,
  onClose,
  onDeleted,
}: {
  conn: ProviderConnection;
  capLabel: (capability: string) => string;
  onClose: () => void;
  onDeleted: (message: string) => void;
}) {
  const [refs, setRefs] = useState<ConnectionReferences | null>(null);
  const [refsError, setRefsError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    modelConfig
      .references(conn.id)
      .then(setRefs)
      .catch(() => setRefsError(true));
  }, [conn.id]);

  const lines = refs ? referenceLines(refs, capLabel) : [];

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await modelConfig.deleteConnection(conn.id);
      onDeleted(
        lines.length
          ? `已删除 ${conn.label}。仍指向它的 ${lines.length} 处会在生成时报错，请改选`
          : `已删除 ${conn.label}`,
      );
    } catch (e) {
      setError(errorText(e, "删除失败"));
      setBusy(false);
    }
  }

  return (
    <ConfirmDialog
      open
      title={`删除供应商 ${conn.label}？`}
      description="删除后这些地方生成时会报错，不会自动换到别的供应商或平台。"
      confirmLabel={busy ? "正在删除…" : "删除"}
      tone="danger"
      confirmDisabled={busy || (!refs && !refsError)}
      disabledReason={!refs && !refsError ? "正在读取谁在使用它…" : undefined}
      cancelLabel="保留"
      onCancel={onClose}
      onConfirm={() => void remove()}
    >
      <div data-testid="delete-refs" className="flex flex-col gap-1.5 text-xs">
        {!refs && !refsError && <p className="text-fg-subtle">正在读取谁在使用它…</p>}
        {refsError && <p className="text-running">读不到使用情况。如果有默认或项目指向它，删除后那里会报错。</p>}
        {refs && lines.length === 0 && <p className="text-fg-muted">没有默认或项目指向它。</p>}
        {refs && lines.length > 0 && (
          <>
            <p className="text-fg">这些地方正在使用它：</p>
            <ul className="list-disc pl-4 text-fg-muted">
              {lines.map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
          </>
        )}
        {error && (
          <p role="alert" className="rounded-md bg-danger-soft px-2.5 py-1.5 text-danger">
            {error}
          </p>
        )}
      </div>
    </ConfirmDialog>
  );
}

// ---------------------------------------------------------------- 还没接入的能力

function PendingSection({ item }: { item: CapabilityConfig }) {
  return (
    <section className="ff-ledger border-dashed">
      <div className="ff-ledger-head border-dashed">
        <h2 className="text-fg-muted">{`${item.label}模型`}</h2>
        <span className="font-normal">M2 待办</span>
      </div>
      <div className="ff-ledger-row">
        <div className="ff-ledger-val">
          {/* 这句由后端给（`unavailable_reason`），前端不自己判断里程碑 */}
          <p className="text-xs leading-5 text-fg-subtle">{item.unavailable_reason}</p>
          <p className="mt-1.5 text-xs leading-5 text-fg-subtle">
            还没有可用的模型，所以这里不放选择器，也不能添加这类供应商。
          </p>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- 平台目录某一家的 Key

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
    <div className="flex flex-col gap-2 border-b border-border px-4 py-3">
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
        保存 Key 不等于改用它计费——选「我的 {providerLabel} 官方 Key」并设为默认后，{capabilityLabel}才会走你自己的账号。
      </p>
      {result && <TestResultLine result={result} />}
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
