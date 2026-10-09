"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { localRuntime, type LocalRuntimeStatus } from "@/lib/api";
import { projectSourceText, providerLines } from "@/lib/freeflow/local-cli";
import { cn } from "@/lib/utils";

/** 比服务端心跳有效期（20 秒）短：连接器一停，十几秒内这里就变成未连接。 */
const REFRESH_MS = 12_000;

/**
 * 模型页上的「本机会员 CLI」（ADR-041）。与上面的「供应商」（API Key）分开放：
 * 那边是网页替你调别人的接口、按 Key 计费；这里是**你自己的电脑**替你跑官方 CLI、
 * 用你的会员额度，不扣平台 Credits。
 *
 * 这里只**看**，不选：选择是逐项目的（项目设置 › 默认模型 › 文本），因为部署只对
 * 白名单内的项目开放，组织默认会把白名单外的项目一起指过来、一跑就报错。
 *
 * 显示的全是后端实况：哪些 CLI 可选、各自连没连上、修复命令、哪个项目在用。
 * 浏览器拿不到桥接令牌、登录凭据或本机路径——状态接口本来就不返回它们。
 */
export function LocalCliSection() {
  const [status, setStatus] = useState<LocalRuntimeStatus | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () =>
      localRuntime
        .status()
        .then((s) => {
          if (!alive) return;
          setStatus(s);
          setFailed(false);
        })
        .catch(() => alive && setFailed(true));
    void load();
    const timer = window.setInterval(() => void load(), REFRESH_MS);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  const enabled = Boolean(status?.enabled);
  const lines = providerLines(status?.text_providers ?? []);

  return (
    <section className="ff-ledger" aria-labelledby="local-cli-head">
      <div className="ff-ledger-head">
        <h2 id="local-cli-head">本机会员 CLI</h2>
        <span className="font-normal">试点 · 逐项目选择 · 只写文本</span>
      </div>
      <p className="ff-ledger-note">
        不用 API Key：在<strong className="font-medium text-fg-muted">你自己的电脑</strong>上运行官方 Claude Code /
        Codex CLI，用你的会员登录写文本。需要电脑开着、本机连接器在运行、CLI 已用会员账号登录；网页拿不到你的登录凭据。
      </p>

      {failed && !status && (
        <p role="alert" className="mx-4 my-2 rounded-md bg-danger-soft px-2.5 py-1.5 text-xs text-danger">
          本机会员 CLI 的状态读取失败，稍后会自动重试。
        </p>
      )}

      {status && !enabled && (
        <div className="ff-ledger-row">
          <div className="ff-ledger-key">状态</div>
          <div className="ff-ledger-val text-xs leading-5 text-fg-subtle">
            这个组织还没有开通。本机会员 CLI 是试点，需要平台为你的组织和具体项目单独开通，开通后这里会显示连接状态。
          </div>
        </div>
      )}

      {enabled &&
        lines.map((line) => (
          <div key={line.provider} className="ff-ledger-row">
            <div className="ff-ledger-key">{line.label}</div>
            <div className="ff-ledger-val">
              <p className="flex items-center gap-2 text-sm text-fg">
                <span
                  aria-hidden
                  className={cn("inline-block size-2 rounded-full", line.tone === "ok" ? "bg-success" : "bg-warning")}
                />
                {line.state}
              </p>
              {line.fix && <p className="mt-1 text-xs leading-5 text-fg-subtle">{line.fix}</p>}
            </div>
          </div>
        ))}

      {enabled && lines.length === 0 && (
        <p className="ff-ledger-empty px-4 py-3 text-xs text-fg-subtle">这个部署没有开放任何本机文本 CLI。</p>
      )}

      {enabled && (
        <div className="ff-ledger-row">
          <div className="ff-ledger-key">在用的项目</div>
          <div className="ff-ledger-val">
            {status && status.projects.length > 0 ? (
              <ul className="flex flex-col gap-1.5">
                {status.projects.map((p) => (
                  <li key={p.project_id} className="flex flex-wrap items-baseline gap-x-2 text-xs leading-5">
                    <span className="max-w-[28ch] truncate text-sm text-fg">{p.title}</span>
                    <span className="text-fg-subtle">{projectSourceText(p.provider)}</span>
                    <Link
                      href={`/freeflow/projects/${p.project_id}/settings`}
                      className="text-primary hover:underline"
                    >
                      去项目设置选择
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs leading-5 text-fg-subtle">还没有开通到任何项目。</p>
            )}
          </div>
        </div>
      )}

      <div className="ff-ledger-row">
        <div className="ff-ledger-key">计费与失败</div>
        <div className="ff-ledger-val text-xs leading-5 text-fg-subtle">
          <p>
            <strong className="font-medium text-fg-muted">文本不扣平台 Credits</strong>，消耗的是你自己的会员额度。
            连接器离线、没登录或额度用完时直接报错，<strong className="font-medium text-fg-muted">不会改用付费模型</strong>。
          </p>
          <p className="mt-1">视频 / 语音：不支持。官方 CLI 没有经过验证的会员视频生成入口，这里不提供视频选项。</p>
        </div>
      </div>

      <details className="ff-ledger-row group">
        <summary className="ff-ledger-key cursor-pointer list-none text-primary hover:underline">怎么连上</summary>
        <ol className="ff-ledger-val list-decimal space-y-1 pl-4 text-xs leading-5 text-fg-subtle">
          <li>
            装好官方 CLI 并用会员账号登录：<code>claude auth login</code> 或 <code>codex login</code>（API Key 登录会被拒绝）。
          </li>
          <li>
            体检（不连服务器、不花额度）：<code>python -m apps.local_runner --doctor</code>
          </li>
          <li>
            把管理员给你的连接令牌放进环境变量 <code>AIGC_LOCAL_RUNNER_TOKEN</code>（网页上不会显示令牌）。
          </li>
          <li>
            启动连接器并保持窗口开着：<code>python -m apps.local_runner --server &lt;服务地址&gt; --provider claude</code>
          </li>
          <li>回到这里看到「已连接」后，到项目设置 › 默认模型 › 文本 选择「本机」。</li>
        </ol>
      </details>
    </section>
  );
}
