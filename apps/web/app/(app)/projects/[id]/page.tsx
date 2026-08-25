"use client";

import { use, useCallback, useEffect, useState } from "react";

import { Clapperboard, Film, ScrollText, Terminal, Users } from "lucide-react";

import { AgentRunsView } from "@/components/project/agent-runs-view";
import { AssetPanel } from "@/components/project/asset-panel";
import { ChatTranscript } from "@/components/project/chat-transcript";
import { CharactersView, charactersMeta } from "@/components/project/characters-view";
import { OutputCard } from "@/components/project/output-card";
import { OutputDrawer } from "@/components/project/output-drawer";
import { PlotIndexView, plotIndexMeta } from "@/components/project/plot-index-view";
import { ProjectRail, episodesOf } from "@/components/project/project-rail";
import { ScenesView, scenesMeta } from "@/components/project/scenes-view";
import { ScreenplayView, screenplayMeta } from "@/components/project/screenplay-view";
import { StaleNotice } from "@/components/project/stale-notice";
import {
  GROUPS,
  TARGET_LABEL,
  type GroupKey,
  type StageKey,
} from "@/components/project/stages";
import { StoryboardView, storyboardMeta } from "@/components/project/storyboard-view";
import { ProjectComposer } from "@/components/project-composer";
import { usePublishWorkspace } from "@/components/shell/workspace";
import {
  ApiRequestError,
  projects,
  type AgentRun,
  type Approval,
  type ChatMessage,
  type Project,
  type ReviseTarget,
} from "@/lib/api";
import { useRenders } from "@/lib/useRenders";

/**
 * 同步指令：让某一步照最新的上游重跑一遍。
 *
 * 走的是普通的 revise，没有专门的"同步"接口。上游数据由后端
 * `orchestrator.variables_for` 从最新 state 现取，revise 里带的
 * "当前产出"只是给模型对照的旧版——所以一句话就够了。
 */
const SYNC_INSTRUCTION = "请根据最新的上游设定同步更新这部分内容，其余保持不变。";

/**
 * 项目工作台：项目栏 | 会话与产出 | 资产栏。
 *
 * 三栏各自滚动，外层不滚——外层一滚，钉在中栏底部的输入框就会跟着跑掉。
 * 阶段进度不在这个页面里，它在左侧外壳的「流程」列表上（`components/
 * shell/sidebar.tsx`），页面只负责把当前阶段登记到 workspace context。
 *
 * **中栏只有对话**。每跑完一步在对话流里留一张结果卡片，完整内容点开
 * 在抽屉里看——产出曾经是中栏里一列折叠面板，但那样中栏同时是对话又是
 * 文档容器，两个身份互相挤，分镜表还被压到要横向滚才看得全。
 */
export default function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);

  const [project, setProject] = useState<Project | null>(null);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState<GroupKey | null>(null);
  const [episode, setEpisode] = useState<number | null>(null);

  // 抽屉里正在看哪一组产出。null = 关着
  const [openGroup, setOpenGroup] = useState<GroupKey | null>(null);

  // 出图：角色基准立绘和分镜出图共用一份状态，进度走项目的 SSE 通道，
  // 和任务中心看到的是同一个数字。
  const renders = useRenders(id);

  const reload = useCallback(async () => {
    const [p, r, a, c] = await Promise.all([
      projects.get(id),
      projects.runs(id),
      projects.approvals(id),
      projects.conversation(id).catch(() => [] as ChatMessage[]),
    ]);
    setProject(p);
    setRuns(r);
    setApprovals(a);
    setMessages(c);
  }, [id]);

  useEffect(() => {
    reload().catch((e) =>
      setError(e instanceof ApiRequestError ? e.error.user_message : "加载失败"),
    );
  }, [reload]);

  const pending = approvals.find((a) => a.status === "pending") ?? null;

  // 按 agent_id 取，不按 role——同一个 role 下现在有多个 Agent。
  // runs 按 created_at 倒序，find 拿到的就是最新一版（含聊天修订后的）。
  const outputOf = (agentId: string) =>
    runs.find((r) => r.agent_id === agentId && r.output_json)?.output_json;

  const output: Record<ReviseTarget, any> = {
    plot_index: outputOf("story.plot_index.v1"),
    screenplay: outputOf("story.screenplay.v1"),
    characters: outputOf("visual.character.v1"),
    scenes: outputOf("visual.scene.v1"),
    storyboard: outputOf("visual.storyboard.v1"),
  };
  const router = outputOf("router.default.v1");

  // 阶段由已有产出推导，不额外维护一份前端状态——
  // 状态的唯一权威在后端（ADR-008），前端再存一份必然对不上。
  const stage: StageKey = pending
    ? pending.gate === "setup"
      ? "await_setup"
      : "await_storyboard"
    : output.storyboard
      ? "done"
      : output.scenes
        ? "storyboard"
        : output.characters
          ? "scenes"
          : output.screenplay
            ? "characters"
            : output.plot_index
              ? "screenplay"
              : router
                ? "plot_index"
                : "routing";

  const episodes = episodesOf(output.screenplay);
  const selected = episodes.find((e) => e.index === episode) ?? null;

  // 顶栏面包屑和左栏「流程」都读这份登记
  usePublishWorkspace(
    project
      ? {
          id,
          title: project.title,
          stage,
          episodeLabel: selected ? `第 ${selected.index} 集 · ${selected.title}` : null,
        }
      : null,
  );

  // 左栏「流程」和右栏「看档案」都是 `#group-xxx` 锚点——中栏已经没有
  // 对应的 DOM 了，所以这里把 hash 接过来当"打开哪一组抽屉"用。
  // 仍然用锚点而不是回调：外壳在这个页面的上层，回调传不下去。
  useEffect(() => {
    const openFromHash = () => {
      const hash = window.location.hash;
      if (!hash.startsWith("#group-")) return;
      setOpenGroup(hash.slice("#group-".length) as GroupKey);
      // 清掉 hash，否则同一组关掉之后再点一次不会触发 hashchange
      history.replaceState(null, "", window.location.pathname + window.location.search);
    };
    openFromHash();
    window.addEventListener("hashchange", openFromHash);
    return () => window.removeEventListener("hashchange", openFromHash);
  }, []);

  // 有产出才能改，顺序与生产顺序一致
  const revisable = (Object.keys(TARGET_LABEL) as ReviseTarget[]).filter((r) => output[r]);
  const staleRoles = project?.stale_roles ?? [];

  /** 把这一组里过期的阶段逐个同步。多个的话按生产顺序来，上游先。 */
  async function syncGroup(key: GroupKey, roles: readonly ReviseTarget[]) {
    setSyncing(key);
    setError(null);
    try {
      for (const role of roles.filter((r) => staleRoles.includes(r))) {
        await projects.revise(id, role, SYNC_INSTRUCTION);
      }
      await reload();
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.error.user_message : "同步失败");
    } finally {
      setSyncing(null);
    }
  }

  const drawer = openGroup ? GROUP_META[openGroup] : null;
  const drawerStale =
    openGroup && openGroup !== "runs"
      ? GROUPS.find((g) => g.key === openGroup)?.roles.some((r) => staleRoles.includes(r))
      : false;

  return (
    // 三栏加起来有个下限（236 + 440 + 352）。窗口比它窄时让**工作区自己**
    // 横向滚，而不是让整个 body 横向滚——body 一横滚，顶栏也会跟着跑掉。
    <div className="h-full min-h-0 overflow-x-auto">
      <div className="flex h-full min-w-[1040px]">
        <ProjectRail
          projectId={id}
          current={project}
          episodes={episodes}
          selectedEpisode={episode}
          onSelectEpisode={setEpisode}
          renderCount={renders.count}
        />

        <section className="flex min-h-0 min-w-[440px] flex-1 flex-col bg-bg">
          <div className="min-h-0 flex-1 overflow-y-auto px-6 pt-5 pb-2">
            <div className="mx-auto flex w-full max-w-[680px] flex-col gap-3.5">
              {(error || renders.error) && (
                <p
                  role="alert"
                  className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger"
                >
                  {error ?? renders.error}
                </p>
              )}

              <ChatTranscript messages={messages} />

              {/* 每跑完一步在对话里留一张结果卡片。已经跑出来的东西永远在，
                  点开在抽屉里看——不存在"生成完就回不去了" */}
              {GROUPS.map((g) => {
                if (g.roles.every((r) => !output[r])) return null;
                const stale = g.roles.some((r) => staleRoles.includes(r));
                const info = GROUP_META[g.key];

                return (
                  <OutputCard
                    key={g.key}
                    icon={info.icon}
                    title={g.label}
                    meta={metaOf(g.key, output)}
                    stale={stale}
                    onOpen={() => setOpenGroup(g.key)}
                    hint={stale ? "上游改过，这一步的内容还是旧的" : undefined}
                    action={
                      stale ? (
                        <StaleNotice
                          busy={syncing === g.key}
                          onSync={() => void syncGroup(g.key, g.roles)}
                        />
                      ) : undefined
                    }
                  />
                );
              })}

              {/* 诊断信息。跟产出同级摆着，但它不是产出，所以放最后 */}
              {runs.length > 0 && (
                <OutputCard
                  icon={GROUP_META.runs.icon}
                  title="Agent 运行"
                  meta={`${runs.length} 次`}
                  onOpen={() => setOpenGroup("runs")}
                />
              )}
            </div>
          </div>

          <div className="shrink-0 px-6 pt-2 pb-5">
            <ProjectComposer
              projectId={id}
              available={revisable}
              pending={pending}
              canAdvance={stage !== "done" && !pending}
              onChanged={reload}
            />
          </div>
        </section>

        <AssetPanel characters={output.characters} scenes={output.scenes} renders={renders} />
      </div>

      <OutputDrawer
        open={openGroup !== null}
        title={drawer?.label ?? ""}
        meta={openGroup && openGroup !== "runs" ? metaOf(openGroup, output) : `${runs.length} 次`}
        onClose={() => setOpenGroup(null)}
        action={
          drawerStale && openGroup && openGroup !== "runs" ? (
            <StaleNotice
              busy={syncing === openGroup}
              onSync={() => {
                const g = GROUPS.find((x) => x.key === openGroup);
                if (g) void syncGroup(g.key, g.roles);
              }}
            />
          ) : undefined
        }
      >
        {openGroup === "runs" ? (
          <AgentRunsView runs={runs} />
        ) : openGroup ? (
          <div className="flex flex-col divide-y divide-border">
            {(GROUPS.find((g) => g.key === openGroup)?.roles ?? [])
              .filter((r) => output[r])
              .map((r) => (
                <section key={r} aria-label={TARGET_LABEL[r]}>
                  {r === "plot_index" && <PlotIndexView data={output.plot_index} />}
                  {r === "screenplay" && (
                    <ScreenplayView data={output.screenplay} episodeIndex={episode} />
                  )}
                  {/* 角色的出图按钮只在右栏，这里是纯档案 */}
                  {r === "characters" && <CharactersView data={output.characters} />}
                  {r === "scenes" && <ScenesView data={output.scenes} />}
                  {r === "storyboard" && (
                    <StoryboardView data={output.storyboard} renders={renders} />
                  )}
                </section>
              ))}
          </div>
        ) : null}
      </OutputDrawer>
    </div>
  );
}

/** 每一组在卡片和抽屉标题上的图标与名字。图标只是认路用，别赋予含义。 */
const GROUP_META: Record<GroupKey, { label: string; icon: typeof ScrollText }> = {
  story: { label: "故事", icon: ScrollText },
  characters: { label: "角色", icon: Users },
  scenes: { label: "场景", icon: Film },
  storyboard: { label: "分镜", icon: Clapperboard },
  runs: { label: "Agent 运行", icon: Terminal },
};

/** 组头上的一行摘要。折起来的时候它是唯一的信息，别省。 */
function metaOf(key: GroupKey, output: Record<ReviseTarget, any>): string {
  if (key === "story") {
    return [
      output.plot_index && plotIndexMeta(output.plot_index),
      output.screenplay && screenplayMeta(output.screenplay),
    ]
      .filter(Boolean)
      .join(" · ");
  }
  if (key === "characters") return output.characters ? charactersMeta(output.characters) : "";
  if (key === "scenes") return output.scenes ? scenesMeta(output.scenes) : "";
  if (key === "storyboard") return output.storyboard ? storyboardMeta(output.storyboard) : "";
  return "";
}
