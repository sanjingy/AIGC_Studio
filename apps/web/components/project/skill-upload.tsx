"use client";

import { useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, X } from "lucide-react";

import { ApiRequestError, orgSkills, type OrgSkill } from "@/lib/api";

/** 上传成功后广播一声，正在列 Skill 的地方（资产库「技能」chip）跟着刷新。 */
export const SKILLS_CHANGED = "skills:changed";

export function notifySkillsChanged() {
  window.dispatchEvent(new Event(SKILLS_CHANGED));
}

/**
 * 传一份 Skill YAML 上去。
 *
 * 只读文件、发原文，解析和校验全在后端——前端照着 `skills/spec.py` 再写
 * 一套校验，两套规则迟早分叉，而那套规则是安全边界（处理器白名单、
 * 导出路径白名单），分叉的后果不是显示不一致而是放行不该放行的东西。
 *
 * 挂成一个 hook 而不是组件：触发按钮和结果提示不在同一处（资产库
 * 「技能」chip 里按钮在标题行、提示在列表上方，空态里两者又都在卡片内），
 * 组件包不住这种布局差异。
 *
 * 入口在**资产库的「技能」chip**（决策记录 §11.5 裁决 8）。原来那个
 * 「对话框 + 附件菜单」的入口随旧壳一起删了。
 */
export function useSkillUpload() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<OrgSkill | null>(null);
  const [error, setError] = useState<string | null>(null);

  function pick() {
    setError(null);
    setResult(null);
    inputRef.current?.click();
  }

  function read(file: File) {
    const looksYaml = /\.(ya?ml)$/i.test(file.name);
    if (!looksYaml) {
      setError("Skill 必须是 .yaml 或 .yml 文件");
      return;
    }

    const reader = new FileReader();
    reader.onerror = () => setError("读取文件失败，请重试");
    reader.onload = async () => {
      const text = typeof reader.result === "string" ? reader.result : "";
      setBusy(true);
      try {
        const row = await orgSkills.upload(text);
        setResult(row);
        notifySkillsChanged();
      } catch (e) {
        // 只有"根本不是 YAML"和"太大"会走到这里；校验不过是 201 + invalid
        setError(e instanceof ApiRequestError ? e.error.user_message : "上传失败");
      } finally {
        setBusy(false);
      }
    };
    reader.readAsText(file);
  }

  const input = (
    <input
      ref={inputRef}
      type="file"
      accept=".yaml,.yml"
      className="hidden"
      onChange={(e) => {
        const file = e.target.files?.[0];
        // 清空以便连续选同一个文件也能再次触发 onChange
        e.target.value = "";
        if (file) read(file);
      }}
    />
  );

  const notice =
    busy || error || result ? (
      <SkillUploadNotice
        busy={busy}
        error={error}
        result={result}
        onDismiss={() => {
          setError(null);
          setResult(null);
        }}
      />
    ) : null;

  return { pick, input, notice, busy };
}

function SkillUploadNotice({
  busy,
  error,
  result,
  onDismiss,
}: {
  busy: boolean;
  error: string | null;
  result: OrgSkill | null;
  onDismiss: () => void;
}) {
  if (busy) {
    return (
      <p className="flex items-center gap-1.5 rounded-lg bg-surface-2 px-2.5 py-1.5 text-xs text-fg-muted">
        <Loader2 aria-hidden className="size-3 animate-spin" />
        正在校验 Skill…
      </p>
    );
  }

  if (error) {
    return (
      <p
        role="alert"
        className="flex items-start gap-1.5 rounded-lg bg-danger-soft px-2.5 py-1.5 text-xs text-danger"
      >
        <AlertTriangle aria-hidden className="mt-0.5 size-3 shrink-0" />
        <span className="flex-1">{error}</span>
        <DismissButton onClick={onDismiss} />
      </p>
    );
  }

  if (!result) return null;

  const ok = result.status === "valid";

  return (
    <div
      role="status"
      className="rounded-[2px] border border-border bg-surface px-3 py-2.5 text-xs"
    >
      <div className="flex items-center gap-1.5">
        {ok ? (
          <CheckCircle2 aria-hidden className="size-3.5 shrink-0 text-success" />
        ) : (
          <AlertTriangle aria-hidden className="size-3.5 shrink-0 text-danger" />
        )}
        <span className="font-semibold text-fg">{result.name}</span>
        <span className="text-fg-subtle">{result.version}</span>
        <span className={ok ? "text-success" : "text-danger"}>
          {ok ? "校验通过" : "校验未通过"}
        </span>
        <span className="ml-auto">
          <DismissButton onClick={onDismiss} />
        </span>
      </div>

      {ok ? (
        <p className="mt-1.5 text-fg-subtle">
          {result.stage_count} 个阶段，{result.gates.length} 道门
          {result.route ? `，${result.route}` : ""}
        </p>
      ) : (
        // 错误原文照实显示。后端已经把 pydantic 的报告压成了逐行的中文，
        // 换成"格式有误"这种笼统文案，用户就没法知道该改哪一行。
        <pre className="mt-1.5 max-h-40 overflow-auto rounded-md bg-surface-2 p-2 text-xs leading-5 whitespace-pre-wrap text-fg-muted">
          {result.validation_errors}
        </pre>
      )}

      {ok && result.missing_agents.length > 0 && (
        <p className="mt-1.5 text-running">
          引用了注册表里没有的 Agent：{result.missing_agents.join("、")}
        </p>
      )}

      {ok && !result.runtime_wired && (
        // ADR-026 的验收标准里明写着这句话必须有：本轮只做到"能传、能选"，
        // 生产流程仍然走后端硬编码的阶段图。不说清楚就是一个假功能。
        <p className="mt-1.5 rounded-md bg-surface-2 px-2 py-1.5 text-fg-subtle">
          已存入技能库，但<span className="text-fg-muted">运行时尚未接线</span>
          ——当前生产流程仍走内置阶段图，这个 Skill 暂时不会生效。
        </p>
      )}
    </div>
  );
}

function DismissButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label="关闭提示"
      className="cursor-pointer text-fg-subtle transition-colors duration-150 hover:text-fg"
    >
      <X aria-hidden className="size-3" />
    </button>
  );
}
