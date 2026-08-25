"use client";

import { useEffect, useRef, useState } from "react";
import {
  ArrowUp,
  Check,
  FileText,
  Loader2,
  Plus,
  RotateCcw,
  Sparkles,
  Wrench,
  X,
} from "lucide-react";

import { useSkillUpload } from "@/components/project/skill-upload";
import { TARGET_LABEL } from "@/components/project/stages";
import { Button } from "@/components/ui/button";
import { ApiRequestError, projects, type Approval, type ReviseTarget } from "@/lib/api";
import { cn } from "@/lib/utils";

const SAMPLE =
  "把这篇小说做成 5 分钟悬疑漫剧，主角是一名侦探，故事发生在雾锁的旧码头。";

/**
 * 上传小说正文时的字符上限，与后端 `AdvanceIn.user_input` 的 `max_length`
 * 对齐（apps/api/modules/agent/schemas.py）。改这里必须同步改后端，否则
 * 前端截断值和服务端校验会对不上。
 */
const MAX_INPUT = 20_000;

/** 审核门摘要的字段名。摘要是后端 `_gate_summary` 给的，键是固定的几个。 */
const GATE_FIELD: Record<string, string> = {
  title: "标题",
  synopsis: "梗概",
  episodes: "集数",
  scenes: "场次",
  nodes_total: "情节节点",
  nodes_covered: "已覆盖节点",
  characters: "角色",
  nodes: "分镜节点",
  shots: "镜号",
};

/**
 * 一个输入框，管所有事。
 *
 * 之前是两个框：上面"创作需求"负责推进，下面 ReviseChat 负责修改。
 * 用户的原话是「把上传的框和 AI 聊天框合并」——两个长得一样的输入框
 * 摆在同一页上，没人知道该往哪个里打字。
 *
 * 合并之后行为分两种，由"有没有产出"决定，而不是由用户去选：
 *
 *   没有产出 → 发送 = advance()，这句话就是创作需求
 *   有了产出 → 上方 pill 选改哪一步，发送 = revise()；
 *              另有一个「继续生成」按钮负责往下推进
 *
 * 「继续生成」不能省：审核门通过之后，得再调一次 advance 才会跑下一批，
 * 只留 revise 的话流程会卡在门后面出不来。
 *
 * 会话记录**不在这里**：它跟产出一起在中栏滚动区里（`ChatTranscript`），
 * 输入框自己钉在栏底。所以这个组件不取会话，改完只喊一声 `onChanged()`，
 * 由页面统一重取——两处各取一次，必然出现一处已更新一处还是旧的。
 */
export function ProjectComposer({
  projectId,
  available,
  pending,
  canAdvance,
  onChanged,
}: {
  projectId: string;
  /** 已有产出、因而可被修订的阶段，按生产顺序 */
  available: ReviseTarget[];
  pending: Approval | null;
  /** 还有没有下一步可跑（跑完了或卡在门上就没有） */
  canAdvance: boolean;
  onChanged: () => void | Promise<void>;
}) {
  const [target, setTarget] = useState<ReviseTarget | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState<null | "send" | "advance" | "gate">(null);
  const [error, setError] = useState<string | null>(null);
  // 上传截断这类"非错误"的提示，跟 error 分开，免得把正常操作画成红色告警
  const [notice, setNotice] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const skill = useSkillUpload();

  // 点别处关掉附件菜单。不做这个，菜单会一直挂在那儿挡住输入框
  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMenuOpen(false);
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  const mode: "advance" | "revise" = available.length === 0 ? "advance" : "revise";
  // 默认改最靠后的那个阶段——用户刚看到的就是它。用派生值而不是 effect
  // 同步 state：available 变化时不必等一次额外渲染。
  const active = target && available.includes(target) ? target : (available.at(-1) ?? null);

  async function act(label: "send" | "advance" | "gate", fn: () => Promise<unknown>) {
    if (busy) return;
    setBusy(label);
    setError(null);
    try {
      await fn();
      await onChanged();
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.error.user_message : "操作失败");
      throw e;
    } finally {
      setBusy(null);
    }
  }

  async function send() {
    const text = draft.trim();
    if (!text && mode === "revise") return;
    setDraft("");
    try {
      await act("send", () =>
        mode === "advance"
          ? projects.advance(projectId, text)
          : projects.revise(projectId, active!, text),
      );
    } catch {
      setDraft(text); // 失败时把输入还回去，不让用户重打一遍
    }
  }

  /**
   * 读一个本地 .txt 文件填进输入框（不自动发送）。只当纯 UTF-8 文本处理，
   * 不做 GBK 等编码转换。内容超过 MAX_INPUT 会截断并提示；选到非文本文件
   * 或读取失败都走 error 明说，不静默。
   */
  function readTxt(file: File) {
    setError(null);
    setNotice(null);

    const looksTxt = /\.txt$/i.test(file.name) || file.type === "text/plain";
    if (!looksTxt) {
      setError("只支持 .txt 纯文本文件");
      return;
    }

    const reader = new FileReader();
    reader.onerror = () => setError("读取文件失败，请重试");
    reader.onload = () => {
      const text = typeof reader.result === "string" ? reader.result : "";
      // NUL 字符在正常文本里不会出现，据此挡下改了后缀的二进制文件
      if (text.indexOf(String.fromCharCode(0)) !== -1) {
        setError("这个文件看起来不是纯文本（可能是二进制文件）");
        return;
      }
      // 按码点截断，与后端 len() 语义一致，也不会切断代理对
      const chars = Array.from(text);
      if (chars.length > MAX_INPUT) {
        setDraft(chars.slice(0, MAX_INPUT).join(""));
        setNotice(
          `小说超过 ${MAX_INPUT.toLocaleString()} 字，已截断到前 ${MAX_INPUT.toLocaleString()} 字。`,
        );
      } else {
        setDraft(text);
      }
    };
    reader.readAsText(file);
  }

  const sendDisabled =
    !!busy ||
    (mode === "revise" && (!active || !draft.trim())) ||
    (mode === "advance" && !!pending);

  return (
    <div className="mx-auto flex w-full max-w-[680px] flex-col gap-2">
      {pending && (
        <GateBlock
          approval={pending}
          busy={busy === "gate"}
          disabled={!!busy}
          onDecide={(decision, comment) =>
            void act("gate", () =>
              projects.resolve(projectId, pending.id, decision, comment),
            ).catch(() => {})
          }
        />
      )}

      {error && (
        <p
          role="alert"
          className="rounded-lg bg-danger-soft px-2.5 py-1.5 text-xs text-danger"
        >
          {error}
        </p>
      )}

      {notice && (
        <p
          role="status"
          className="rounded-lg bg-surface-2 px-2.5 py-1.5 text-xs text-fg-muted"
        >
          {notice}
        </p>
      )}

      {skill.notice}

      {mode === "revise" && (
        <div
          className="flex flex-wrap items-center gap-1 px-0.5"
          role="group"
          aria-label="改哪一步"
        >
          <span className="mr-1 text-xs text-fg-subtle">改</span>
          {available.map((r) => (
            <button
              key={r}
              type="button"
              aria-pressed={active === r}
              onClick={() => setTarget(r)}
              className={cn(
                "cursor-pointer rounded-md px-2 py-0.5 text-xs transition-colors duration-150",
                active === r
                  ? "bg-primary-soft font-medium text-primary"
                  : "text-fg-subtle hover:bg-surface-2 hover:text-fg",
              )}
            >
              {TARGET_LABEL[r]}
            </button>
          ))}
        </div>
      )}

      <div className="rounded-2xl border border-border-strong bg-surface px-3.5 pt-3 pb-2.5 shadow-[0_1px_2px_rgba(15,23,42,.04)]">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            // Enter 发送，Shift+Enter 换行。输入法组字中的 Enter 不算
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void send();
            }
          }}
          rows={mode === "advance" ? 3 : 2}
          disabled={!!busy}
          aria-label={mode === "advance" ? "创作需求" : `修改${active ? TARGET_LABEL[active] : ""}`}
          placeholder={
            mode === "advance"
              ? "描述你想做什么，或直接粘贴小说正文。Enter 发送"
              : `说说${active ? TARGET_LABEL[active] : ""}要怎么改，Enter 发送`
          }
          className="w-full resize-none bg-transparent text-sm text-fg outline-none placeholder:text-fg-subtle disabled:opacity-45"
        />

        <div className="mt-1.5 flex flex-wrap items-center gap-2">
          {/* 附件菜单。txt 是「这一轮要处理的素材」，skill 是「以后都按这条
              生产线走」——两件事差别很大，但都是"从本地拿个文件进来"，
              收在同一个 + 里比在输入框边上排两个文字按钮清楚。 */}
          <div ref={menuRef} className="relative">
            <button
              type="button"
              onClick={() => setMenuOpen((v) => !v)}
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              aria-label="添加内容"
              disabled={!!busy}
              className="flex size-7 cursor-pointer items-center justify-center rounded-lg bg-surface-2 text-fg-muted transition-colors duration-150 hover:text-fg disabled:opacity-45"
            >
              <Plus aria-hidden className="size-4" />
            </button>

            {menuOpen && (
              <div
                role="menu"
                className="absolute bottom-9 left-0 z-20 w-52 overflow-hidden rounded-xl border border-border bg-surface py-1 shadow-lg"
              >
                {/* 小说正文只在"还没有产出"时有意义：已经跑出剧本之后再塞
                    一篇正文进来，走的是 revise，那不是它的用法。 */}
                {mode === "advance" && (
                  <MenuItem
                    icon={FileText}
                    label="上传小说 txt"
                    hint="读进输入框，不自动发送"
                    onClick={() => {
                      setMenuOpen(false);
                      fileRef.current?.click();
                    }}
                  />
                )}
                <MenuItem
                  icon={Wrench}
                  label="上传 Skill"
                  hint=".yaml，校验后存入技能库"
                  onClick={() => {
                    setMenuOpen(false);
                    skill.pick();
                  }}
                />
              </div>
            )}
          </div>

          {/* 视觉隐藏，由菜单项触发点击 */}
          <input
            ref={fileRef}
            type="file"
            accept=".txt,text/plain"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              // 清空以便连续选同一个文件也能再次触发 onChange
              e.target.value = "";
              if (file) readTxt(file);
            }}
          />
          {skill.input}

          {mode === "advance" && !draft && !busy && (
            <button
              type="button"
              onClick={() => setDraft(SAMPLE)}
              className="cursor-pointer text-xs text-fg-subtle underline-offset-2 transition-colors duration-150 hover:text-fg hover:underline"
            >
              填入示例
            </button>
          )}

          {mode === "revise" && canAdvance && (
            <Button
              size="sm"
              disabled={!!busy}
              onClick={() =>
                void act("advance", () => projects.advance(projectId, "")).catch(() => {})
              }
            >
              {busy === "advance" ? (
                <Loader2 aria-hidden className="size-3.5 animate-spin" />
              ) : (
                <Sparkles aria-hidden className="size-3.5" />
              )}
              {busy === "advance" ? "生成中…" : "继续生成"}
            </Button>
          )}

          <span className="ml-auto min-w-0 truncate text-xs text-fg-subtle">
            {pending
              ? "先处理上面的确认"
              : mode === "advance"
                ? "一次点击会一路跑到下一个确认门"
                : "每次发送都是一次真实调用，会消耗 Credits"}
          </span>

          <button
            type="button"
            onClick={() => void send()}
            disabled={sendDisabled}
            aria-label={mode === "advance" ? "开始生成" : "发送"}
            className="flex size-7.5 shrink-0 cursor-pointer items-center justify-center rounded-lg bg-primary text-primary-fg transition-colors duration-150 hover:bg-primary-hover disabled:pointer-events-none disabled:opacity-45"
          >
            {busy === "send" ? (
              <Loader2 aria-hidden className="size-4 animate-spin" />
            ) : (
              <ArrowUp aria-hidden className="size-4" />
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

function MenuItem({
  icon: Icon,
  label,
  hint,
  onClick,
}: {
  icon: typeof FileText;
  label: string;
  hint: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className="flex w-full cursor-pointer items-start gap-2.5 px-3 py-2 text-left transition-colors duration-150 hover:bg-surface-2"
    >
      <Icon aria-hidden className="mt-0.5 size-3.5 shrink-0 text-fg-muted" />
      <span className="min-w-0">
        <span className="block text-xs font-medium text-fg">{label}</span>
        <span className="block text-xs text-fg-subtle">{hint}</span>
      </span>
    </button>
  );
}

/** 审核门。钉在输入框正上方，不会被折叠或滚走。 */
function GateBlock({
  approval,
  busy,
  disabled,
  onDecide,
}: {
  approval: Approval;
  busy: boolean;
  disabled: boolean;
  onDecide: (decision: string, comment?: string) => void;
}) {
  const summary = approval.payload_json.summary ?? {};

  return (
    <div className="rounded-xl border border-border bg-surface p-3">
      <div className="flex items-baseline gap-2">
        <h3 className="text-sm font-semibold text-fg">
          {approval.gate === "setup" ? "确认剧本" : "确认分镜"}
        </h3>
        <span className="text-xs text-fg-subtle">通过后才会继续消耗 Credits</span>
      </div>

      <div className="mt-2 flex flex-col gap-1.5">
        {Object.entries(summary)
          .filter(([, v]) => typeof v === "string" && v)
          .map(([k, v]) => (
            <p key={k} className="text-xs text-fg-muted">
              <span className="text-fg-subtle">{GATE_FIELD[k] ?? k}：</span>
              {String(v)}
            </p>
          ))}
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(summary)
            .filter(([, v]) => typeof v === "number")
            .map(([k, v]) => (
              <span key={k} className="rounded bg-surface-2 px-1.5 py-0.5 text-xs text-fg-subtle">
                {GATE_FIELD[k] ?? k} <span className="tnum text-fg">{String(v)}</span>
              </span>
            ))}
        </div>
      </div>

      <div className="mt-2.5 flex flex-wrap gap-2">
        <Button
          variant="primary"
          size="sm"
          disabled={disabled}
          onClick={() => onDecide("approved")}
        >
          {busy ? (
            <Loader2 aria-hidden className="size-3.5 animate-spin" />
          ) : (
            <Check aria-hidden className="size-3.5" />
          )}
          通过
        </Button>
        <Button
          size="sm"
          disabled={disabled}
          onClick={() => onDecide("changes_requested", "需要重做")}
        >
          <RotateCcw aria-hidden className="size-3.5" />
          打回重做
        </Button>
        <Button variant="ghost" size="sm" disabled={disabled} onClick={() => onDecide("rejected")}>
          <X aria-hidden className="size-3.5" />
          拒绝
        </Button>
      </div>
    </div>
  );
}
