"use client";

import { use, useCallback, useEffect, useState } from "react";
import { Check, RotateCcw, Sparkles, X } from "lucide-react";

import { ReviseChat } from "@/components/revise-chat";
import { Button } from "@/components/ui/button";
import { Panel, PanelHeader } from "@/components/ui/panel";
import {
  ApiRequestError,
  projects,
  type AgentRun,
  type Approval,
  type Project,
  type ReviseTarget,
} from "@/lib/api";

const STAGE_STEPS = [
  { key: "routing", label: "路线" },
  { key: "story", label: "故事" },
  { key: "await_setup", label: "确认设定" },
  { key: "visual", label: "视觉" },
  { key: "await_storyboard", label: "确认分镜" },
  { key: "done", label: "完成" },
];

const SAMPLE =
  "把这篇小说做成 5 分钟悬疑漫剧，主角是一名侦探，故事发生在雾锁的旧码头。";

export default function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);

  const [project, setProject] = useState<Project | null>(null);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [input, setInput] = useState(SAMPLE);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    const [p, r, a] = await Promise.all([
      projects.get(id),
      projects.runs(id),
      projects.approvals(id),
    ]);
    setProject(p);
    setRuns(r);
    setApprovals(a);
  }, [id]);

  useEffect(() => {
    reload().catch((e) =>
      setError(e instanceof ApiRequestError ? e.error.user_message : "加载失败"),
    );
  }, [reload]);

  async function run<T>(label: string, fn: () => Promise<T>) {
    setBusy(label);
    setError(null);
    try {
      await fn();
      await reload();
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.error.user_message : "操作失败");
    } finally {
      setBusy(null);
    }
  }

  const pending = approvals.find((a) => a.status === "pending");

  const story = runs.find((r) => r.role === "story" && r.output_json)?.output_json;
  const router = runs.find((r) => r.role === "router" && r.output_json)?.output_json;
  const visual = runs.find((r) => r.role === "visual" && r.output_json)?.output_json;

  // 阶段由已有产出推导，不额外维护一份前端状态——
  // 状态的唯一权威在后端（ADR-008），前端再存一份必然对不上。
  const stage = pending
    ? pending.gate === "setup"
      ? "await_setup"
      : "await_storyboard"
    : visual
      ? "done"
      : story
        ? "visual"
        : router
          ? "story"
          : "routing";
  const stageIndex = STAGE_STEPS.findIndex((s) => s.key === stage);

  // 有产出才能改。顺序与生产顺序一致，聊天框默认选最靠后的那个
  const revisable: ReviseTarget[] = [
    ...(story ? (["story"] as const) : []),
    ...(visual ? (["visual"] as const) : []),
  ];

  return (
    <div className="mx-auto flex max-w-[1000px] flex-col gap-4">
      <Panel>
        <PanelHeader
          title={project?.title ?? "加载中…"}
          meta={router ? `${router.route} · ${router.estimated_shots} 镜` : undefined}
        />
        <ol className="flex items-center gap-1 px-3 py-2" aria-label="生产阶段">
          {STAGE_STEPS.map((s, i) => (
            <li key={s.key} className="flex items-center gap-1">
              {i > 0 && <span aria-hidden className="h-px w-4 bg-border-strong" />}
              <span
                className={
                  i < stageIndex
                    ? "rounded-full px-2 py-0.5 text-xs text-success"
                    : i === stageIndex
                      ? "rounded-full bg-primary-soft px-2 py-0.5 text-xs font-medium text-primary"
                      : "rounded-full px-2 py-0.5 text-xs text-fg-subtle"
                }
              >
                {i < stageIndex && <Check aria-hidden className="mr-0.5 inline size-3" />}
                {s.label}
              </span>
            </li>
          ))}
        </ol>
      </Panel>

      {error && (
        <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}

      {/* 输入与推进 */}
      <Panel>
        <PanelHeader title="创作需求" meta="真实 LLM 调用，会消耗 Credits" />
        <div className="flex flex-col gap-3 p-3">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            rows={3}
            className="rounded-md border border-border-strong bg-surface px-2.5 py-2 text-sm text-fg placeholder:text-fg-subtle"
            placeholder="描述你想做什么，比如：把这篇小说做成 5 分钟悬疑漫剧"
          />
          <div className="flex items-center gap-2">
            <Button
              variant="primary"
              disabled={!!busy || !!pending}
              onClick={() => run("advance", () => projects.advance(id, input))}
            >
              <Sparkles aria-hidden className="size-3.5" />
              {busy === "advance" ? "生成中…" : pending ? "先处理下面的确认" : "开始 / 继续"}
            </Button>
            <span className="text-xs text-fg-subtle">
              一次点击会一路跑到下一个确认门
            </span>
          </div>
        </div>
      </Panel>

      {/* 审核门 */}
      {pending && (
        <Panel className="border-primary/40">
          <PanelHeader
            title={pending.gate === "setup" ? "确认设定" : "确认分镜"}
            meta="通过后才会继续消耗 Credits"
          />
          <div className="flex flex-col gap-3 p-3">
            <pre className="overflow-x-auto rounded-md bg-surface-2 p-2.5 text-xs text-fg-muted">
              {JSON.stringify(pending.payload_json.summary, null, 2)}
            </pre>
            <div className="flex gap-2">
              <Button
                variant="primary"
                disabled={!!busy}
                onClick={() =>
                  run("approve", () => projects.resolve(id, pending.id, "approved"))
                }
              >
                <Check aria-hidden className="size-3.5" />
                通过
              </Button>
              <Button
                disabled={!!busy}
                onClick={() =>
                  run("changes", () =>
                    projects.resolve(id, pending.id, "changes_requested", "需要重做"),
                  )
                }
              >
                <RotateCcw aria-hidden className="size-3.5" />
                打回重做
              </Button>
              <Button
                variant="ghost"
                disabled={!!busy}
                onClick={() =>
                  run("reject", () => projects.resolve(id, pending.id, "rejected"))
                }
              >
                <X aria-hidden className="size-3.5" />
                拒绝
              </Button>
            </div>
          </div>
        </Panel>
      )}

      {/* 产出 */}
      {story && (
        <Panel>
          <PanelHeader title={String(story.title)} meta={`${story.acts?.length ?? 0} 幕`} />
          <div className="flex flex-col gap-2 p-3">
            <p className="text-sm text-fg-muted">{String(story.logline)}</p>
            <p className="text-xs text-fg-subtle">核心冲突：{String(story.central_conflict)}</p>
            <ol className="mt-1 flex flex-col gap-1.5">
              {(story.acts ?? []).map((a: any) => (
                <li key={a.index} className="rounded-md bg-surface-2 px-2.5 py-2">
                  <div className="flex items-baseline gap-2">
                    <span className="tnum text-xs text-fg-subtle">{a.index}</span>
                    <span className="text-sm font-medium">{a.title}</span>
                    <span className="text-xs text-fg-subtle">{a.mood}</span>
                  </div>
                  <p className="mt-0.5 text-xs text-fg-muted">{a.summary}</p>
                </li>
              ))}
            </ol>
          </div>
        </Panel>
      )}

      {visual && (
        <Panel>
          <PanelHeader
            title="视觉设定"
            meta={`${visual.characters?.length ?? 0} 角色 · ${visual.shots?.length ?? 0} 镜`}
          />
          <div className="flex flex-col gap-3 p-3">
            {(visual.characters ?? []).map((c: any) => (
              <div key={c.ref} className="rounded-md bg-surface-2 px-2.5 py-2">
                <div className="text-sm font-medium">
                  {c.name}
                  <span className="ml-2 text-xs text-fg-subtle">{c.ref}</span>
                </div>
                <p className="mt-0.5 text-xs text-fg-muted">
                  {[c.age_range, c.hair, c.eyes, c.face, c.build, c.outfit, c.distinctive]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              </div>
            ))}
            <ol className="flex flex-col gap-1">
              {(visual.shots ?? []).slice(0, 12).map((s: any) => (
                <li key={s.index} className="flex gap-2 text-xs">
                  <span className="tnum w-6 shrink-0 text-fg-subtle">{s.index}</span>
                  <span className="w-12 shrink-0 text-fg-subtle">{s.shot_size}</span>
                  <span className="text-fg-muted">{s.content}</span>
                </li>
              ))}
            </ol>
          </div>
        </Panel>
      )}

      {/* 聊天修订 */}
      <ReviseChat projectId={id} available={revisable} onRevised={reload} />

      {/* Agent 运行记录 */}
      <Panel>
        <PanelHeader title="Agent 运行" meta={`${runs.length} 次`} />
        {runs.length === 0 ? (
          <p className="px-3 py-6 text-center text-sm text-fg-subtle">还没有运行记录</p>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border text-left text-fg-subtle">
                <th className="px-3 py-1.5 font-medium">角色</th>
                <th className="px-3 py-1.5 font-medium">状态</th>
                <th className="px-3 py-1.5 font-medium">模型</th>
                <th className="px-3 py-1.5 text-right font-medium">token</th>
                <th className="px-3 py-1.5 font-medium">错误</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id} className="border-b border-border last:border-0">
                  <td className="px-3 py-1.5">{r.role}</td>
                  <td className="px-3 py-1.5">
                    <span
                      className={
                        r.status === "succeeded"
                          ? "text-success"
                          : r.status === "failed"
                            ? "text-danger"
                            : "text-running"
                      }
                    >
                      {r.status}
                    </span>
                  </td>
                  <td className="px-3 py-1.5 text-fg-muted">{r.model_id ?? "—"}</td>
                  <td className="tnum px-3 py-1.5 text-right text-fg-muted">
                    {r.tokens_in}→{r.tokens_out}
                  </td>
                  <td className="px-3 py-1.5 text-danger">{r.error_code ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}
