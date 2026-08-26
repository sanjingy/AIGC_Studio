"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Check, Eye, EyeOff, Loader2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Panel, PanelHeader } from "@/components/ui/panel";
import {
  ApiRequestError,
  modelCatalog,
  providerCredentials,
  type CapabilityModels,
  type KeyTestResult,
  type ProviderCredential,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 05 模型（全局层）。
 *
 * 这一页回答两个问题，都用真数据：**每个能力能选哪些模型**（`/model-catalog`）
 * 和**这个能力用谁的账号计费**（`/provider-credentials`，就是设置页
 * 「模型密钥」那套接口，组件形态照抄，不重新发明一遍）。
 *
 * 三条自我约束：
 *
 * - **档位说明只讲模型定位，不讲价格。** "更省钱"这种结论一旦冻进前端，
 *   上游调一次价它就变成谎话（DeepSeek 2026-08-17 涨过 350%）。
 *   钱的事在 `model_pricing` 表和 19_UnitEconomics.md。
 * - **没接入的能力照实说，不给下拉框。** 视频和语音在目录里 `available=false`，
 *   连带原因一起由后端给。放一个选了不生效的下拉框比不放更糟。
 * - **切换动作不在这一页。** 模型覆盖是项目级的（ADR-024），这里没有项目
 *   上下文，所以只展示「能选什么」，选在项目设置里做。写清楚去哪选，
 *   而不是在这里放一个改不了任何东西的控件。
 */
export function ModelCatalogPage() {
  const [items, setItems] = useState<CapabilityModels[] | null>(null);
  const [keys, setKeys] = useState<ProviderCredential[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  /** 同时只展开一个 Key 表单，避免两个输入框里都躺着一把明文 */
  const [editing, setEditing] = useState<string | null>(null);

  const loadKeys = () =>
    providerCredentials
      .list()
      .then((r) => setKeys(r.items))
      // Key 列表拿不到不该让整页空白：模型目录本身还是有用的
      .catch(() => setKeys([]));

  useEffect(() => {
    modelCatalog
      .list()
      .then((r) => setItems(r.items))
      .catch((e) =>
        setError(e instanceof ApiRequestError ? e.error.user_message : "加载模型目录失败"),
      );
    void loadKeys();
  }, []);

  if (error && !items) {
    return (
      <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
        {error}
      </p>
    );
  }

  if (!items) return <p className="text-sm text-fg-muted">加载中…</p>;

  const available = items.filter((i) => i.available);
  const pending = items.filter((i) => !i.available);

  return (
    <div className="mx-auto flex w-full max-w-[820px] flex-col gap-3">
      <Panel>
        <PanelHeader
          title="模型"
          meta={`${available.length} 个能力已接入`}
        />
        <p className="px-3 py-2.5 text-xs leading-5 text-fg-subtle">
          每个能力有哪些模型可选，以及这个能力用谁的账号计费。
          <strong className="font-medium text-fg-muted">
            具体用哪个模型是按项目设的
          </strong>
          （同一个账号下不同项目可以不一样），切换入口在项目 › 设置 › 基础设置。
          没有单独设置的项目走系统默认档。
        </p>
      </Panel>

      {notice && (
        <p role="status" className="rounded-md bg-surface-2 px-3 py-2 text-xs text-fg-muted">
          {notice}
        </p>
      )}

      {available.map((item) => (
        <CapabilitySection
          key={item.capability}
          item={item}
          credential={keys.find((k) => k.capability === item.capability) ?? null}
          open={editing === item.capability}
          onToggle={() => {
            setNotice(null);
            setEditing(editing === item.capability ? null : item.capability);
          }}
          onSaved={(saved) => {
            setKeys((prev) =>
              prev.some((k) => k.capability === saved.capability)
                ? prev.map((k) => (k.capability === saved.capability ? saved : k))
                : [...prev, saved],
            );
            setEditing(null);
            setNotice(`${saved.label}已改用你自己的 ${saved.provider_label} 账号计费`);
          }}
          onRemoved={async (cap, label) => {
            await providerCredentials.remove(cap);
            setNotice(`已移除${label}的密钥，该能力退回平台默认额度`);
            await loadKeys();
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

function CapabilitySection({
  item,
  credential,
  open,
  onToggle,
  onSaved,
  onRemoved,
}: {
  item: CapabilityModels;
  credential: ProviderCredential | null;
  open: boolean;
  onToggle: () => void;
  onSaved: (saved: ProviderCredential) => void;
  onRemoved: (capability: string, label: string) => Promise<void>;
}) {
  const [confirming, setConfirming] = useState(false);
  const configured = credential?.configured ?? false;

  return (
    <Panel>
      <PanelHeader
        title={`${item.label}模型`}
        meta={item.provider_label ?? undefined}
        action={
          <span className="rounded-full bg-surface-2 px-2 py-0.5 text-xs whitespace-nowrap text-fg-subtle">
            {item.models.length} 档可选
          </span>
        }
      />

      <ul className="flex flex-col divide-y divide-border">
        {item.models.map((m) => (
          <li key={m.model_id} className="flex flex-wrap items-baseline gap-x-2 gap-y-1 px-3 py-2.5">
            <span className="text-sm font-medium text-fg">{m.label}</span>
            {/* 模型 id 用等宽：它是精确值，逐字符核对时才对得齐 */}
            <span className="font-mono text-xs text-fg-muted">{m.model_id}</span>
            {m.model_id === item.default_model_id && (
              <span className="rounded-full bg-primary-soft px-2 py-0.5 text-xs text-primary">
                默认
              </span>
            )}
            <span className="w-full text-xs leading-5 text-fg-subtle">{m.note}</span>
          </li>
        ))}
      </ul>

      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-t border-border px-3 py-2.5">
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className="text-xs font-medium text-fg">计费账号</span>
          {configured ? (
            <span className="font-mono text-xs text-fg-muted">
              {credential?.masked_key ?? "已保存"}
            </span>
          ) : (
            <span className="text-xs text-fg-subtle">使用平台默认额度计费</span>
          )}
        </div>

        <div className="flex items-center gap-2">
          <span className="rounded-full bg-surface-2 px-2 py-0.5 text-xs whitespace-nowrap text-fg-subtle">
            {configured ? `已配置 · ${item.provider_label}` : "未配置"}
          </span>
          {confirming ? (
            <>
              <Button
                variant="danger"
                size="sm"
                onClick={() => {
                  setConfirming(false);
                  void onRemoved(item.capability, item.label);
                }}
              >
                确认移除
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setConfirming(false)}>
                取消
              </Button>
            </>
          ) : (
            <>
              <Button size="sm" onClick={onToggle} aria-expanded={open}>
                {configured ? "更换密钥" : "配置密钥"}
              </Button>
              {configured && (
                <Button variant="ghost" size="sm" onClick={() => setConfirming(true)}>
                  移除
                </Button>
              )}
            </>
          )}
        </div>
      </div>

      {open && (
        <KeyForm
          capability={item.capability}
          capabilityLabel={item.label}
          providerLabel={item.provider_label ?? ""}
          onSaved={onSaved}
          onCancel={onToggle}
        />
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------- 还没接入的能力

function PendingSection({ item }: { item: CapabilityModels }) {
  return (
    <Panel className="border-dashed">
      <PanelHeader title={`${item.label}模型`} meta="暂未接入" />
      <div className="px-3 py-2.5">
        {/* 这句由后端给（`unavailable_reason`）。让前端自己判断"视频排在哪个
            里程碑"，等于把一句迟早会过期的话冻进前端文案。 */}
        <p className="text-xs leading-5 text-fg-subtle">{item.unavailable_reason}</p>
        <p className="mt-1.5 text-xs leading-5 text-fg-subtle">
          目录里还没有这个能力的任何 Provider，所以这里
          <strong className="font-medium text-fg-muted">不放下拉框也不放密钥入口</strong>
          ——选了不会生效，配了也不会被用到。
        </p>
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------- Key 表单
// 与 /settings/keys 的 KeyForm 同形：同一套接口、同一套交互。
// 那一页仍然是账号级密钥的主入口，这里只是把它放到模型旁边。

function KeyForm({
  capability,
  capabilityLabel,
  providerLabel,
  onSaved,
  onCancel,
}: {
  capability: string;
  capabilityLabel: string;
  providerLabel: string;
  onSaved: (saved: ProviderCredential) => void;
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
      if (kind === "test") setResult(await providerCredentials.test(capability, key));
      else onSaved(await providerCredentials.put(capability, key));
    } catch (e) {
      setError(
        e instanceof ApiRequestError
          ? e.error.user_message
          : kind === "test"
            ? "测试失败"
            : "保存失败",
      );
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-col gap-2 border-t border-border px-3 py-2.5">
      <label htmlFor={`key-${capability}`} className="text-xs font-medium text-fg">
        {providerLabel} API Key
      </label>

      <div className="relative">
        <input
          id={`key-${capability}`}
          type={visible ? "text" : "password"}
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            setResult(null);
          }}
          autoComplete="off"
          spellCheck={false}
          placeholder="粘贴你的 API Key"
          className={cn(
            "h-9 w-full rounded-md border border-border-strong bg-surface pr-9 pl-2.5",
            "font-mono text-sm text-fg placeholder:font-sans placeholder:text-fg-subtle",
            "transition-colors duration-150 hover:border-fg-subtle",
          )}
        />
        <button
          type="button"
          onClick={() => setVisible(!visible)}
          aria-label={visible ? "隐藏密钥" : "显示密钥"}
          aria-pressed={visible}
          className="absolute top-1/2 right-1.5 flex size-6 -translate-y-1/2 cursor-pointer items-center justify-center rounded text-fg-subtle transition-colors duration-150 hover:bg-surface-2 hover:text-fg"
        >
          {visible ? (
            <EyeOff aria-hidden className="size-3.5" />
          ) : (
            <Eye aria-hidden className="size-3.5" />
          )}
        </button>
      </div>

      <p className="text-xs leading-5 text-fg-subtle">
        密钥会被加密后保存，服务端无法再读出明文；提交前可以先测试连接。配置后，
        {capabilityLabel}的调用将改走你自己的账号计费。这是账号级设置，对本账号
        所有项目生效——与「用哪个模型」是两件事，后者按项目设。
      </p>

      {result && (
        <p
          role="status"
          className={cn(
            "flex items-start gap-1.5 rounded-md px-2.5 py-1.5 text-xs",
            result.ok ? "bg-success-soft text-success" : "bg-danger-soft text-danger",
          )}
        >
          {result.ok ? (
            <Check aria-hidden className="mt-px size-3.5 shrink-0" />
          ) : (
            <X aria-hidden className="mt-px size-3.5 shrink-0" />
          )}
          <span className="min-w-0 break-all">{result.message}</span>
        </p>
      )}

      {error && (
        <p role="alert" className="rounded-md bg-danger-soft px-2.5 py-1.5 text-xs text-danger">
          {error}
        </p>
      )}

      <div className="flex items-center gap-2">
        <Button
          variant="primary"
          size="sm"
          disabled={!key || busy !== null}
          onClick={() => void run("save")}
        >
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
        <Link
          href="/settings/keys"
          className="ml-auto text-xs text-fg-subtle underline-offset-2 hover:text-fg hover:underline"
        >
          在设置页统一管理
        </Link>
      </div>
    </div>
  );
}
