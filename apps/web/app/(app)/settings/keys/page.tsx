"use client";

import { useEffect, useState } from "react";
import { Check, Eye, EyeOff, Loader2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Panel, PanelHeader } from "@/components/ui/panel";
import {
  ApiRequestError,
  providerCredentials,
  type KeyTestResult,
  type ProviderCredential,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { PageScroll } from "@/components/shell/page-scroll";

/**
 * 模型密钥设置（ADR-025）。
 *
 * 账号级而不是项目级：一把 Key 服务这个 org 的所有项目。
 *
 * 视觉上刻意保持成一个**普通设置页**：没配自己的 Key 是完全正常的默认状态，
 * 用警示色框住它只会制造焦虑。danger 色在这一页只出现在两个地方——
 * 测试连接失败的原因，和"确认移除"这一步。
 */
export default function KeysPage() {
  const [items, setItems] = useState<ProviderCredential[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  /** 正在编辑哪个能力的 Key。同时只展开一个，避免两个表单里都躺着一把明文 */
  const [editing, setEditing] = useState<string | null>(null);

  const load = () =>
    providerCredentials
      .list()
      .then((r) => setItems(r.items))
      .catch((e) =>
        setError(e instanceof ApiRequestError ? e.error.user_message : "加载密钥配置失败"),
      );

  useEffect(() => {
    void load();
  }, []);

  async function afterSave(saved: ProviderCredential) {
    setItems((prev) =>
      (prev ?? []).map((i) => (i.capability === saved.capability ? saved : i)),
    );
    setEditing(null);
    setNotice(`${saved.label}已改用你自己的 ${saved.provider_label} 账号计费`);
  }

  async function remove(item: ProviderCredential) {
    try {
      await providerCredentials.remove(item.capability);
      // 退回平台计费是用户移除 Key 时本来就想要的结果，不值得弹窗强调
      setNotice(`已移除${item.label}的密钥`);
      await load();
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.error.user_message : "移除失败");
    }
  }

  if (error && !items) {
    return (
      <p role="alert" className="m-4 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
        {error}
      </p>
    );
  }

  if (!items) return <p className="p-4 text-sm text-fg-muted">加载中…</p>;

  return (
    <PageScroll>
      <div className="mx-auto flex max-w-[720px] flex-col gap-3">
        <Panel>
          <PanelHeader title="模型密钥" meta={`${items.filter((i) => i.configured).length}/${items.length} 已配置`} />
          <p className="px-3 py-2.5 text-xs text-fg-subtle">
            配置自己的上游账号后，对应能力的调用将改走你的账号计费，平台只按存储、
            审核这类实际发生的成本收取少量 Credits。未配置的能力使用平台默认额度。
          </p>
        </Panel>

        {notice && (
          <p
            role="status"
            className="rounded-md bg-surface-2 px-3 py-2 text-xs text-fg-muted"
          >
            {notice}
          </p>
        )}
        {error && items && (
          <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-xs text-danger">
            {error}
          </p>
        )}

        {items.map((item) => (
          <CapabilityCard
            key={item.capability}
            item={item}
            open={editing === item.capability}
            onOpen={() => {
              setNotice(null);
              setEditing(editing === item.capability ? null : item.capability);
            }}
            onSaved={afterSave}
            onRemove={() => remove(item)}
          />
        ))}
      </div>
    </PageScroll>
  );
}

// ---------------------------------------------------------------- 单个能力

function CapabilityCard({
  item,
  open,
  onOpen,
  onSaved,
  onRemove,
}: {
  item: ProviderCredential;
  open: boolean;
  onOpen: () => void;
  onSaved: (saved: ProviderCredential) => void;
  onRemove: () => void;
}) {
  // 移除走两段式：点一次变成"确认移除"，再点才真删。
  // 二次确认对话框这个仓库里还没有，为一个删除按钮引进一套是杀鸡用牛刀。
  const [confirming, setConfirming] = useState(false);

  return (
    <Panel>
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 px-3 py-2.5">
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className="text-sm font-medium text-fg">{item.label}</span>
          {item.configured ? (
            <span className="font-mono text-xs text-fg-muted">
              {item.masked_key ?? "已保存"}
            </span>
          ) : (
            <span className="text-xs text-fg-subtle">使用平台默认额度计费</span>
          )}
        </div>

        <div className="flex items-center gap-2">
          {/* 静态状态用 surface-2 药丸，不抢 primary——primary 留给当前可交互的选中态 */}
          <span
            className={cn(
              "rounded-full px-2 py-0.5 text-xs whitespace-nowrap",
              "bg-surface-2 text-fg-subtle",
            )}
          >
            {item.configured ? `已配置 · ${item.provider_label}` : "未配置"}
          </span>

          {confirming ? (
            <>
              <Button
                variant="danger"
                size="sm"
                onClick={() => {
                  setConfirming(false);
                  onRemove();
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
              <Button size="sm" onClick={onOpen} aria-expanded={open}>
                {item.configured ? "更换" : "配置"}
              </Button>
              {item.configured && (
                <Button variant="ghost" size="sm" onClick={() => setConfirming(true)}>
                  移除
                </Button>
              )}
            </>
          )}
        </div>
      </div>

      {open && <KeyForm item={item} onSaved={onSaved} onCancel={onOpen} />}
    </Panel>
  );
}

// ---------------------------------------------------------------- 输入表单

function KeyForm({
  item,
  onSaved,
  onCancel,
}: {
  item: ProviderCredential;
  onSaved: (saved: ProviderCredential) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState("");
  // 只影响这一次输入的展示。跟"保存后还能不能看到"无关——
  // 保存之后服务端根本不会把明文吐回来，前端拿到的永远是尾号。
  const [visible, setVisible] = useState(false);
  const [busy, setBusy] = useState<"test" | "save" | null>(null);
  const [result, setResult] = useState<KeyTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const key = value.trim();

  async function test() {
    setBusy("test");
    setResult(null);
    setError(null);
    try {
      setResult(await providerCredentials.test(item.capability, key));
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.error.user_message : "测试失败");
    } finally {
      setBusy(null);
    }
  }

  async function save() {
    setBusy("save");
    setError(null);
    try {
      onSaved(await providerCredentials.put(item.capability, key));
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.error.user_message : "保存失败");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-col gap-2 border-t border-border px-3 py-2.5">
      <label htmlFor={`key-${item.capability}`} className="text-xs font-medium text-fg">
        {item.provider_label} API Key
      </label>

      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <input
            id={`key-${item.capability}`}
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
              // 等宽字体：Key 和成本、镜号一样是"精确值"，逐字符核对时才对得齐
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
      </div>

      <p className="text-xs text-fg-subtle">
        密钥会被加密后保存，服务端无法再读出明文；提交前可以先测试连接。配置后，
        {item.label}的调用将改走你自己的账号计费。
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
          {/* 失败时展示后端给的具体原因：Key 错了、额度用完了还是别的，
              用户需要知道的正是这句 */}
          <span className="min-w-0 break-all">{result.message}</span>
        </p>
      )}

      {error && (
        <p role="alert" className="rounded-md bg-danger-soft px-2.5 py-1.5 text-xs text-danger">
          {error}
        </p>
      )}

      <div className="flex items-center gap-2">
        {/* 保存不要求先测通过：用户可能就是想先存下来晚点再测，
            上游临时抽风也不该拦着他保存 */}
        <Button variant="primary" size="sm" disabled={!key || busy !== null} onClick={save}>
          {busy === "save" && <Loader2 aria-hidden className="size-3.5 animate-spin" />}
          保存
        </Button>
        <Button size="sm" disabled={!key || busy !== null} onClick={test}>
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
