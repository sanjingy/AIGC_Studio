import type { LocalCliName, LocalRuntimeStatus, LocalTextProvider } from "../api";

/**
 * 本机会员 CLI 写文本（ADR-041）的纯逻辑：引用串、项目设置里的选项、模型页的状态行。
 *
 * 三条规矩在这里收口：
 *
 * 1. **选项只来自后端实况**。哪几个 CLI 可选是部署配的（`text_providers`），
 *    能不能点是连接器心跳报的（`ready`）。前端不写死"Codex 可用"——用户的 Codex
 *    没 `codex login` 时连接器起不来，这里就只会是灰的、带修复命令。
 * 2. **只对白名单内的项目出现**。项目不在 `project_ids` 里，选项一个都不给。
 * 3. **没有视频**。官方 CLI 没有经过验证的会员视频生成入口，不放任何视频选项。
 */

export const LOCAL_PREFIX = "provider.local:";

export const CLI_LABEL: Record<LocalCliName, string> = {
  claude: "Claude（Claude Code 会员）",
  codex: "Codex（ChatGPT 会员）",
};

/** 官方登录命令。只写命令，不写路径、不写令牌。 */
export const LOGIN_COMMAND: Record<LocalCliName, string> = {
  claude: "claude auth login",
  codex: "codex login",
};

export function localRef(provider: LocalCliName): string {
  return `${LOCAL_PREFIX}${provider}`;
}

/** 不是本机引用时返回 null；前缀对、名字认不出时原样返回名字（后端会报不可用）。 */
export function parseLocalRef(value: string | null | undefined): string | null {
  if (!value || !value.startsWith(LOCAL_PREFIX)) return null;
  return value.slice(LOCAL_PREFIX.length);
}

export function cliLabel(name: string): string {
  return (CLI_LABEL as Record<string, string>)[name] ?? name;
}

function inScope(status: LocalRuntimeStatus | null, projectId: string | null): boolean {
  return Boolean(status?.enabled && projectId && status.project_ids.includes(projectId));
}

export type LocalTextOption = { value: string; text: string; disabled: boolean };

/**
 * 项目设置 › 默认模型 › 文本 下拉里的「本机会员 CLI」一组。
 * 未连接的仍列出来但不可选：用户要知道"有这条路、现在为什么用不了"。
 */
export function localTextOptions(status: LocalRuntimeStatus | null, projectId: string | null): LocalTextOption[] {
  if (!inScope(status, projectId)) return [];
  return (status?.text_providers ?? []).map((p) => ({
    value: localRef(p.provider),
    text: `本机 ${CLI_LABEL[p.provider]} · 不扣平台 Credits${p.ready ? "" : "（未连接）"}`,
    disabled: !p.ready,
  }));
}

/** 项目当前选了本机时，下拉下面那句说明。状态拿不到时只讲规则，不猜在不在线。 */
export function localSelectionHint(value: string | null | undefined, status: LocalRuntimeStatus | null): string {
  const name = parseLocalRef(value) ?? "";
  const label = cliLabel(name);
  const row = status?.text_providers.find((p) => p.provider === name);
  if (status && status.enabled && !row) {
    return `这个项目存着「本机 ${label}」，但它已不在可选范围内，文本生成会直接报错，请改选`;
  }
  if (row && !row.ready) {
    return `你电脑上的 ${label} 现在不在线：文本生成会直接报错，不会改用付费模型。${row.reason ?? ""}`;
  }
  return `用你电脑上的 ${label} 写文本：不扣平台 Credits，消耗你自己的会员额度。电脑和本机连接器要保持在线，出错直接报错，不会换成付费模型`;
}

export type ProviderLine = {
  provider: LocalCliName;
  label: string;
  /** 一句状态：已连接 · 版本 / 未连接 */
  state: string;
  tone: "ok" | "warn";
  /** 不可用时怎么修；可用时 null */
  fix: string | null;
  login: string;
};

export function providerLines(rows: LocalTextProvider[]): ProviderLine[] {
  return rows.map((p) => ({
    provider: p.provider,
    label: CLI_LABEL[p.provider],
    state: p.ready
      ? `已连接${p.version ? ` · ${p.version}` : ""} · 可写文本`
      : p.connected
        ? "连接器在线，但没有报告文本能力"
        : "未连接",
    tone: p.ready ? "ok" : "warn",
    fix: p.ready ? null : p.reason,
    login: LOGIN_COMMAND[p.provider],
  }));
}

/** 模型页上「哪个项目在用哪个」的一句。 */
export function projectSourceText(provider: LocalCliName | null): string {
  return provider ? `文本用 本机 ${CLI_LABEL[provider]}` : "未使用本机（文本走平台或自带 Key 模型）";
}
