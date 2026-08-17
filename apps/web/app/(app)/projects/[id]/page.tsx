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
  { key: "plot_index", label: "情节目录" },
  { key: "screenplay", label: "剧本" },
  { key: "await_setup", label: "确认剧本" },
  { key: "characters", label: "角色" },
  { key: "scenes", label: "场景" },
  { key: "storyboard", label: "分镜" },
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

  // 按 agent_id 取，不按 role——同一个 role 下现在有多个 Agent。
  // runs 按 created_at 倒序，find 拿到的就是最新一版（含聊天修订后的）。
  const outputOf = (agentId: string) =>
    runs.find((r) => r.agent_id === agentId && r.output_json)?.output_json;

  const router = outputOf("router.default.v1");
  const plotIndex = outputOf("story.plot_index.v1");
  const screenplay = outputOf("story.screenplay.v1");
  const characters = outputOf("visual.character.v1");
  const scenes = outputOf("visual.scene.v1");
  const storyboard = outputOf("visual.storyboard.v1");

  // 阶段由已有产出推导，不额外维护一份前端状态——
  // 状态的唯一权威在后端（ADR-008），前端再存一份必然对不上。
  const stage = pending
    ? pending.gate === "setup"
      ? "await_setup"
      : "await_storyboard"
    : storyboard
      ? "done"
      : scenes
        ? "storyboard"
        : characters
          ? "scenes"
          : screenplay
            ? "characters"
            : plotIndex
              ? "screenplay"
              : router
                ? "plot_index"
                : "routing";
  const stageIndex = STAGE_STEPS.findIndex((s) => s.key === stage);

  // 有产出才能改。顺序与生产顺序一致，聊天框默认选最靠后的那个
  const revisable: ReviseTarget[] = [
    ...(plotIndex ? (["plot_index"] as const) : []),
    ...(screenplay ? (["screenplay"] as const) : []),
    ...(characters ? (["characters"] as const) : []),
    ...(scenes ? (["scenes"] as const) : []),
    ...(storyboard ? (["storyboard"] as const) : []),
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
            title={pending.gate === "setup" ? "确认剧本" : "确认分镜"}
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
      {plotIndex && (
        <Panel>
          <PanelHeader
            title="情节目录"
            meta={`${plotIndex.nodes?.length ?? 0} 个节点 · ${plotIndex.scene_count ?? 0} 场景 · 台词约 ${plotIndex.dialogue_chars ?? 0} 字`}
          />
          <div className="flex flex-col gap-2 p-3">
            <p className="text-sm text-fg-muted">{String(plotIndex.logline ?? "")}</p>
            <p className="text-xs text-fg-subtle">
              核心冲突：{String(plotIndex.central_conflict ?? "")}
            </p>
            <ol className="mt-1 grid grid-cols-1 gap-1 sm:grid-cols-2">
              {(plotIndex.nodes ?? []).map((n: any) => (
                <li key={n.index} className="flex gap-2 text-xs">
                  <span className="tnum w-5 shrink-0 text-fg-subtle">{n.index}</span>
                  <span className="text-fg-muted">{n.summary}</span>
                </li>
              ))}
            </ol>
          </div>
        </Panel>
      )}

      {screenplay && (
        <Panel>
          <PanelHeader
            title={`《${screenplay.title}》`}
            meta={`${screenplay.episodes?.length ?? 0} 集 · 覆盖 ${screenplay.node_coverage?.length ?? 0} 个节点`}
          />
          <div className="flex flex-col gap-3 p-3">
            <p className="text-sm text-fg-muted">{String(screenplay.synopsis ?? "")}</p>
            {(screenplay.episodes ?? []).map((ep: any) => (
              <div key={ep.index} className="flex flex-col gap-2">
                <div className="text-sm font-medium">
                  第 {ep.index} 集 {ep.title}
                </div>
                {(ep.scenes ?? []).map((sc: any) => (
                  <div key={sc.id} className="rounded-md bg-surface-2 px-2.5 py-2">
                    <div className="text-xs text-fg-subtle">
                      {sc.id}　【{sc.location} - {sc.time_mood}】
                    </div>
                    <div className="mt-1 flex flex-col gap-0.5 text-xs">
                      {(sc.beats ?? []).map((b: any, i: number) => (
                        <p key={i} className="text-fg-muted">
                          {b.kind === "action" && `△${b.text}`}
                          {b.kind === "sfx" && `【音效：${b.text}】`}
                          {b.kind === "vo" && (
                            <>
                              <span className="text-fg">{b.character_ref}（VO）</span>：{b.text}
                            </>
                          )}
                          {b.kind === "dialogue" && (
                            <>
                              <span className="text-fg">
                                {b.character_ref}
                                {b.emotion && `（${b.emotion}）`}
                              </span>
                              ：{b.text}
                            </>
                          )}
                        </p>
                      ))}
                      {sc.hook && <p className="text-primary">【钩子】{sc.hook}</p>}
                    </div>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </Panel>
      )}

      {characters && (
        <Panel>
          <PanelHeader
            title="角色档案"
            meta={`${characters.characters?.length ?? 0} 个角色`}
          />
          <div className="flex flex-col gap-2 p-3">
            {(characters.characters ?? []).map((c: any) => (
              <div key={c.ref} className="rounded-md bg-surface-2 px-2.5 py-2">
                <div className="flex flex-wrap items-baseline gap-2 text-sm">
                  <span className="font-medium">{c.name}</span>
                  <span className="text-xs text-fg-subtle">{c.ref}</span>
                  <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-fg-subtle">
                    {c.camp}
                  </span>
                  <span className="text-xs text-fg-subtle">{(c.personality ?? []).join("・")}</span>
                </div>
                {c.present_state && (
                  <p className="mt-0.5 text-xs text-fg-subtle">当前状态：{c.present_state}</p>
                )}
                <p className="mt-0.5 text-xs text-fg-muted">
                  {[
                    c.ethnicity,
                    c.age_range,
                    c.build,
                    c.face,
                    c.hair,
                    c.eyes,
                    c.skin,
                    c.outfit,
                    c.shoes,
                    c.accessories,
                    c.distinctive,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
                {(c.inferred ?? []).length > 0 && (
                  <p className="mt-0.5 text-xs text-fg-subtle">
                    推断字段：{(c.inferred ?? []).join("、")}
                  </p>
                )}
              </div>
            ))}
          </div>
        </Panel>
      )}

      {scenes && (
        <Panel>
          <PanelHeader
            title="场景档案"
            meta={`${scenes.scenes?.length ?? 0} 个场景 · ${scenes.era ?? ""}`}
          />
          <div className="flex flex-col gap-2 p-3">
            {(scenes.scenes ?? []).map((sc: any) => (
              <div key={sc.ref} className="rounded-md bg-surface-2 px-2.5 py-2">
                <div className="flex flex-wrap items-baseline gap-2 text-sm">
                  <span className="font-medium">{sc.name}</span>
                  <span className="text-xs text-fg-subtle">{sc.ref}</span>
                  <span className="text-xs text-fg-subtle">{sc.time_slot}</span>
                </div>
                <p className="mt-0.5 text-xs text-fg-muted">{sc.setting}</p>
                <p className="mt-0.5 text-xs text-fg-subtle">光影：{sc.lighting}</p>
                {sc.camera_axis && (
                  <p className="mt-0.5 text-xs text-fg-subtle">
                    摄影主轴：{sc.camera_axis.position} → {sc.camera_axis.facing} →{" "}
                    {sc.camera_axis.far_end}
                  </p>
                )}
                {(sc.fixed_references ?? []).length > 0 && (
                  <p className="mt-0.5 text-xs text-fg-subtle">
                    固定参照物：{(sc.fixed_references ?? []).join("；")}
                  </p>
                )}
              </div>
            ))}
          </div>
        </Panel>
      )}

      {storyboard && (
        <Panel>
          <PanelHeader
            title="分镜表"
            meta={`${storyboard.nodes?.length ?? 0} 个节点 · ${storyboard.shots?.length ?? 0} 个镜号`}
          />
          {/* 宽表在自己的容器里横向滚动，页面本身不横向滚 */}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px] text-xs">
              <thead>
                <tr className="border-b border-border text-left text-fg-subtle">
                  <th className="px-3 py-1.5 font-medium">镜号</th>
                  <th className="px-2 py-1.5 font-medium">节点</th>
                  <th className="px-2 py-1.5 font-medium">景别</th>
                  <th className="px-2 py-1.5 font-medium">角度</th>
                  <th className="px-2 py-1.5 font-medium">运镜</th>
                  <th className="px-2 py-1.5 font-medium">画面内容</th>
                  <th className="px-2 py-1.5 font-medium">出场人物</th>
                  <th className="px-2 py-1.5 font-medium">场景</th>
                  <th className="px-2 py-1.5 font-medium">对白 / 音效</th>
                </tr>
              </thead>
              <tbody>
                {(storyboard.shots ?? []).map((s: any) => (
                  <tr key={s.index} className="border-b border-border align-top last:border-0">
                    <td className="tnum px-3 py-1.5">{s.index}</td>
                    <td className="tnum px-2 py-1.5 text-fg-subtle">{s.node_index}</td>
                    <td className="px-2 py-1.5 whitespace-nowrap">{s.shot_size}</td>
                    <td className="px-2 py-1.5 text-fg-subtle">{s.angle || "—"}</td>
                    <td className="px-2 py-1.5 whitespace-nowrap text-fg-subtle">
                      {s.camera_move || "—"}
                    </td>
                    <td className="px-2 py-1.5 text-fg-muted">{s.content}</td>
                    <td className="px-2 py-1.5 text-fg-subtle">
                      {(s.character_refs ?? []).join("、") || "—"}
                    </td>
                    <td className="px-2 py-1.5 text-fg-subtle">{s.scene_ref}</td>
                    <td className="px-2 py-1.5 text-fg-muted">
                      {s.dialogue && (
                        <div>
                          {s.speaker_ref}：{s.dialogue}
                        </div>
                      )}
                      {s.sfx && <div className="text-fg-subtle">【{s.sfx}】</div>}
                      {!s.dialogue && !s.sfx && "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
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
