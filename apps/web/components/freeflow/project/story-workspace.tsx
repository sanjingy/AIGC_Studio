"use client";

import { useEffect, useState } from "react";

import { StoryIcon } from "@/components/icons/studio-icons";
import { PlotIndexView, plotIndexMeta } from "@/components/project/plot-index-view";
import { ScreenplayView, screenplayMeta } from "@/components/project/screenplay-view";
import { STORY_ANCHORS } from "@/lib/freeflow/stage-links";
import type { ProjectState } from "@/lib/freeflow/use-project-state";
import { cn } from "@/lib/utils";

import { PlanGate } from "./plan-gate";
import { AdvanceAction, GateActions } from "./production-actions";
import { RevisePanel } from "./revise-panel";

type EpisodeRef = { index: number; title: string };

/**
 * 故事页：情节目录 + 剧本在同一路由（计划 §2 把「分场剧本」并进来）。
 *
 * 原来左边那条可折叠的「故事上下文」栏已经去掉——外壳已经有侧栏和右栏，
 * 再加一条就是第三列，1440 宽下正文会被压到读不了。分集改成正文顶部的
 * 一排 chip，它本来也只是个筛选器。
 */
export function StoryWorkspace({
  projectId,
  state,
}: {
  /** 路由参数里的 id，不从 `state.project` 上取——加载中它还是 null。 */
  projectId: string;
  state: ProjectState;
}) {
  const plotIndex = state.output.plot_index;
  const screenplay = state.output.screenplay;
  const [episodeIndex, setEpisodeIndex] = useState<number | null>(null);

  // 从阶段条点「故事 / 剧本」进来时带着 hash，但内容是异步加载的：浏览器那一次
  // 自动定位发生在 section 还不存在的时候。内容到位后、以及之后每次 hash 变化，
  // 自己定位一次。
  useEffect(() => {
    const go = () => {
      const id = window.location.hash.replace(/^#/, "");
      if (id !== STORY_ANCHORS.story && id !== STORY_ANCHORS.script) return;
      const el = document.getElementById(id);
      if (el) scrollWithinPane(el);
    };
    go();
    window.addEventListener("hashchange", go);
    return () => window.removeEventListener("hashchange", go);
  }, [Boolean(plotIndex), Boolean(screenplay)]);
  const episodes: EpisodeRef[] = Array.isArray(screenplay?.episodes)
    ? screenplay.episodes
        .map((episode: any) => ({
          index: Number(episode?.index),
          title: String(episode?.title ?? "未命名"),
        }))
        .filter((episode: EpisodeRef) => Number.isFinite(episode.index))
    : [];

  if (!plotIndex && !screenplay) {
    return (
      <main className="min-h-0 flex-1 overflow-y-auto p-6">
        <div className="mx-auto flex w-full max-w-[720px] flex-col gap-4">
          <div className="rf-empty-state rounded-[2px] border border-dashed border-border-strong px-6 py-8 text-center">
            <span className="rf-empty-icon"><StoryIcon aria-hidden className="size-7" /></span>
            <h2 className="mt-3 text-sm font-semibold text-fg">还没有故事产出</h2>
            <p className="mt-1 text-sm leading-6 text-fg-subtle">
              给一段小说原文或一句创意，然后推进生产：编排器会依次跑路线判断、情节目录，
              停在「开拍前确认」这道门上——在那里定画风、时代背景与改编模式，然后才跑剧本。
            </p>
          </div>
          <AdvanceAction state={state} />
        </div>
      </main>
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-[1120px] flex-col gap-4 p-4 lg:p-6 2xl:max-w-[1280px]">
        <div>
          <h2 className="text-xl font-semibold text-fg">故事与剧本</h2>
          <p className="mt-1 max-w-2xl text-sm leading-6 text-fg-subtle">
            情节目录与剧本共享同一条生产链。分集筛选只改变当前阅读范围，不写库。
          </p>
        </div>

        {/* 两道门都开在这里，和概览页、右栏是同一个动作——要批的东西就在下面。
            同一时刻只可能开一道（后端一次只建一条 pending approval），
            两个组件各自判断自己那道门开没开，不会同时出现。 */}
        {state.pendingGate === "plan" && <PlanGate projectId={projectId} state={state} />}
        <GateActions state={state} gate="setup" />

        {!state.pendingGate && <AdvanceAction state={state} />}

          {plotIndex && (
            /* id 是门① 那句「全部 N 条就在下面」的落点。锚点而不是回调：
               门在这个组件里、目录也在这个组件里，但两者中间隔着几个兄弟节点，
               而滚动定位本来就是浏览器的事。 */
            <section
              id={STORY_ANCHORS.story}
              className="scroll-mt-4 overflow-hidden rounded-[2px] border border-border bg-surface"
            >
              <div className="flex items-baseline justify-between gap-3 border-b border-border px-4 py-3">
                <h3 className="text-sm font-semibold text-fg">情节目录</h3>
                <span className="tnum text-xs text-fg-subtle">{plotIndexMeta(plotIndex)}</span>
              </div>
              <PlotIndexView data={plotIndex} />
            </section>
          )}

          <RevisePanel state={state} target="plot_index" />

          {screenplay && episodes.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="mr-1 text-xs text-fg-subtle">分集</span>
              <EpisodeChip
                label="全部"
                active={episodeIndex === null}
                onClick={() => setEpisodeIndex(null)}
              />
              {episodes.map((episode) => (
                <EpisodeChip
                  key={episode.index}
                  label={`第 ${episode.index} 集　${episode.title}`}
                  active={episode.index === episodeIndex}
                  onClick={() => setEpisodeIndex(episode.index)}
                />
              ))}
            </div>
          )}

          {screenplay && (
            /* 剧本走纸面：全站唯一的浅色面。剧本本来就是纸，
               这个对比只用在这里，别处不复用。 */
            <section id={STORY_ANCHORS.script} className="ff-paper scroll-mt-4 overflow-hidden">
              <div className="flex items-baseline justify-between gap-3 border-b border-border px-4 py-3">
                <h3 className="text-sm font-semibold text-fg">
                  {episodeIndex === null ? "完整剧本" : `第 ${episodeIndex} 集剧本`}
                </h3>
                <span className="tnum truncate text-xs text-fg-subtle">
                  {screenplayMeta(screenplay)}
                </span>
              </div>
              <ScreenplayView data={screenplay} episodeIndex={episodeIndex} />
            </section>
          )}

          <RevisePanel state={state} target="screenplay" />
      </div>
    </div>
  );
}

/** 分集筛选 chip。只改当前阅读范围，不改任何生产范围，也不写库。 */
function EpisodeChip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "max-w-56 cursor-pointer truncate rounded-[2px] px-2.5 py-1 text-xs transition-colors duration-150",
        active
          ? "bg-primary-soft font-medium text-primary"
          : "bg-surface-2 text-fg-muted hover:bg-surface-3 hover:text-fg",
      )}
      title={label}
    >
      {label}
    </button>
  );
}

/**
 * 只滚动**最近的那个可滚动容器**，不用 `scrollIntoView`。
 *
 * 工作台外壳是 `overflow-hidden` 的定高布局，`scrollIntoView` 会把沿途所有祖先
 * 都滚一遍——包括外壳本身，结果是顶栏和胶片条被推出视口、底部露出一截空白。
 * 浏览器的原生 hash 定位也会这么干，所以顺手把被推走的 overflow-hidden 祖先复位。
 */
function scrollWithinPane(el: HTMLElement) {
  let pane: HTMLElement | null = null;
  for (let node = el.parentElement; node; node = node.parentElement) {
    const overflowY = getComputedStyle(node).overflowY;
    if (!pane && (overflowY === "auto" || overflowY === "scroll") && node.scrollHeight > node.clientHeight) {
      pane = node;
    } else if (overflowY === "hidden" && node.scrollTop !== 0) {
      node.scrollTop = 0;
    }
  }
  if (document.scrollingElement) document.scrollingElement.scrollTop = 0;
  if (!pane) return;
  const offset = el.getBoundingClientRect().top - pane.getBoundingClientRect().top;
  pane.scrollTo({ top: pane.scrollTop + offset - 16 });
}
