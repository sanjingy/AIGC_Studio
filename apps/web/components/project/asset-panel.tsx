"use client";

import { useState } from "react";
import Link from "next/link";
import { Clapperboard, Images, PanelRightClose, PanelRightOpen, Wand2 } from "lucide-react";

import { RenderThumb } from "@/components/project/render-slot";
import { groupDomId } from "@/components/project/stages";
import { cn } from "@/lib/utils";
import type { RenderSubject, Renders, RenderView } from "@/lib/useRenders";

type CharacterCard = {
  ref: string;
  name: string;
  desc: string;
  glyph: string;
};

type SceneCard = {
  ref: string;
  name: string;
  desc: string;
};

/**
 * 右栏：本项目的视觉资产。
 *
 * 角色的出图卡片在这里，**也**在中栏的角色档案里（`CharactersView`）——
 * 两处渲染同一份 `renders` 状态，点哪边都一样，不是两张图。原先这里是
 * 唯一入口，中栏刻意留白；后来场景出图接上时中栏成了它的唯一入口
 * （右栏没有场景卡），两边不对称到用户会以为"角色能出图、场景不能"反过来
 * 也一样——于是干脆两处都放，图比"入口只有一处"这条美学规则更要紧的是
 * "用户在哪个面板都能找到同一个按钮"。
 *
 * 场景仍然只在这里列清单 + 跳转链接，出图动作留在中栏——右栏目前没有
 * 场景卡片组件，加一个是下一步，见 `SceneAssetCard`（如果还没做）。
 * 道具是真没有——`agents/schemas.py` 里根本没有道具这个产出。
 */
export function AssetPanel({
  characters,
  scenes,
  renders,
}: {
  /** `visual.character.v1` 的产出，没跑到就是 null */
  characters: any;
  /** `visual.scene.v1` 的产出 */
  scenes: any;
  renders: Renders;
}) {
  const [tab, setTab] = useState<"pending" | "done">("pending");
  // 分镜表最小 860 宽。1440 的屏上中栏只剩 640，那张表会被压到要横向滚才
  // 看得到「画面」列——出图入口滚出屏幕外，等于没有。收起右栏把 352 还给
  // 中栏，表就放得下了。
  const [collapsed, setCollapsed] = useState(false);

  const chars: CharacterCard[] = (characters?.characters ?? []).map((c: any) => ({
    ref: String(c.ref ?? ""),
    name: String(c.name ?? ""),
    glyph: String(c.name ?? "?").trim().charAt(0),
    desc: [c.age_range, c.build, c.hair, c.outfit, c.distinctive]
      .filter(Boolean)
      .join(" · "),
  }));

  const sceneList: SceneCard[] = (scenes?.scenes ?? []).map((s: any) => ({
    ref: String(s.ref ?? ""),
    name: String(s.name ?? ""),
    desc: String(s.setting ?? ""),
  }));

  const viewOf = (ref: string) => renders.renderOf({ kind: "character", ref });
  const isDone = (ref: string) => viewOf(ref)?.assetId != null;

  const done = chars.filter((c) => isDone(c.ref));
  const pending = chars.filter((c) => !isDone(c.ref));
  const running = chars.some((c) => {
    const v = viewOf(c.ref);
    return v?.status === "queued" || v?.status === "running";
  });
  const visible = tab === "pending" ? pending : done;

  const total = chars.length;
  const percent = total === 0 ? 0 : Math.round((done.length / total) * 100);
  const allDone = total > 0 && done.length === total;

  const batch = pending
    .filter((c) => {
      const v = viewOf(c.ref);
      return !v || v.status === "failed" || v.status === "cancelled";
    })
    .map((c): RenderSubject => ({ kind: "character", ref: c.ref }));

  if (collapsed) {
    return (
      <aside className="flex w-11 shrink-0 flex-col items-center gap-3 border-l border-border bg-surface py-3">
        <button
          type="button"
          onClick={() => setCollapsed(false)}
          aria-expanded={false}
          aria-label="展开项目资产"
          title="展开项目资产"
          className="flex size-7 cursor-pointer items-center justify-center rounded-md text-fg-muted transition-colors duration-150 hover:bg-surface-2 hover:text-fg"
        >
          <PanelRightOpen aria-hidden className="size-4" />
        </button>
        <span
          className="text-xs font-semibold text-fg-subtle"
          style={{ writingMode: "vertical-rl" }}
        >
          项目资产
        </span>
        <span className="tnum text-xs text-fg-subtle">{total}</span>
      </aside>
    );
  }

  return (
    <aside className="flex w-[352px] shrink-0 flex-col border-l border-border bg-surface">
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-3.5">
        <button
          type="button"
          onClick={() => setCollapsed(true)}
          aria-expanded={true}
          aria-label="收起项目资产"
          title="收起，把宽度让给分镜表"
          className="flex size-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-fg-muted transition-colors duration-150 hover:bg-surface-2 hover:text-fg"
        >
          <PanelRightClose aria-hidden className="size-4" />
        </button>
        <span className="text-sm font-semibold">项目资产</span>
        <span className="tnum text-xs text-fg-subtle">{total}</span>
        <Link
          href="/assets"
          className="ml-auto flex h-7 items-center gap-1.5 rounded-lg border border-border-strong px-2.5 text-xs text-fg-muted transition-colors duration-150 hover:bg-surface-2 hover:text-fg"
        >
          <Images aria-hidden className="size-3.5" />
          资源库
        </Link>
      </div>

      {total === 0 ? (
        <p className="px-3.5 py-8 text-center text-xs text-fg-subtle">
          角色阶段跑完后，这里会列出可出图的角色。
        </p>
      ) : (
        <>
          <div className="flex items-center gap-2.5 border-b border-border bg-surface-2 px-3.5 py-2.5">
            <div className="min-w-0 flex-1">
              <div className="text-xs font-medium text-fg">
                {allDone
                  ? "全部角色已出图"
                  : running
                    ? "正在出图…"
                    : `有 ${pending.length} 个角色待出图`}
              </div>
              <div className="text-xs text-fg-subtle">
                {allDone ? "可以开始逐镜出图" : "会调用真实上游，按张扣 Credits"}
              </div>
            </div>
            <button
              type="button"
              disabled={batch.length === 0 || running}
              onClick={() => void renders.generateMany(batch)}
              title="逐个提交，每张都会扣一次 Credits"
              className="flex h-7.5 shrink-0 cursor-pointer items-center gap-1.5 rounded-lg bg-fg px-2.5 text-xs font-medium text-surface transition-opacity duration-150 hover:opacity-90 disabled:pointer-events-none disabled:bg-surface-3 disabled:text-fg-subtle"
            >
              <Wand2 aria-hidden className="size-3.5" />
              {running ? "生成中…" : "全部生成"}
            </button>
          </div>

          <div className="flex gap-1 px-3.5 pt-2.5 pb-1.5">
            <Tab active={tab === "pending"} onClick={() => setTab("pending")}>
              待生成 {pending.length}
            </Tab>
            <Tab active={tab === "done"} onClick={() => setTab("done")}>
              已生成 {done.length}
            </Tab>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-3.5 pt-1.5 pb-3.5">
            <div className="flex flex-col gap-2">
              {visible.length === 0 && (
                <p className="py-6 text-center text-xs text-fg-subtle">
                  {tab === "pending" ? "都出完了" : "还没有出好的图"}
                </p>
              )}
              {visible.map((c) => (
                <CharacterAssetCard
                  key={c.ref}
                  card={c}
                  view={viewOf(c.ref)}
                  busy={renders.isPending({ kind: "character", ref: c.ref })}
                  onGenerate={() => renders.generate({ kind: "character", ref: c.ref })}
                  onRetry={(taskId) => renders.retry({ kind: "character", ref: c.ref }, taskId)}
                />
              ))}
            </div>

            {sceneList.length > 0 && (
              <>
                <div className="flex items-baseline gap-2 px-0.5 pt-4 pb-1.5">
                  <span className="text-xs font-semibold tracking-wider text-fg-subtle">
                    场景
                  </span>
                  <span className="tnum text-xs text-fg-subtle">{sceneList.length}</span>
                  <a
                    href={`#${groupDomId("scenes")}`}
                    className="ml-auto text-xs text-fg-muted underline-offset-2 transition-colors duration-150 hover:text-fg hover:underline"
                  >
                    去档案里出图
                  </a>
                </div>
                <ul className="flex flex-col gap-1">
                  {sceneList.map((s) => (
                    <li
                      key={s.ref}
                      className="rounded-lg border border-border bg-surface-2 px-2.5 py-2"
                    >
                      <div className="flex items-baseline gap-2">
                        <span className="truncate text-xs font-medium text-fg">{s.name}</span>
                        <span className="shrink-0 text-xs text-fg-subtle">{s.ref}</span>
                      </div>
                      <p className="mt-0.5 line-clamp-2 text-xs text-fg-subtle">{s.desc}</p>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>

          <div className="shrink-0 border-t border-border px-3.5 py-3">
            <div className="flex items-center justify-between text-xs text-fg-subtle">
              <span>
                角色出图 {done.length}/{total}
              </span>
              <span className="tnum">{percent}%</span>
            </div>
            <div className="my-2 h-1 overflow-hidden rounded-full bg-surface-2">
              <div
                role="progressbar"
                aria-valuenow={percent}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label="角色出图进度"
                className="h-full bg-primary transition-[width] duration-300"
                style={{ width: `${percent}%` }}
              />
            </div>
            <button
              type="button"
              disabled
              title="视频生成属于 M2（TTS → 时间线 → ffmpeg 合成），还没做"
              className="flex h-9 w-full items-center justify-center gap-2 rounded-lg bg-surface-2 text-sm font-semibold text-fg-subtle"
            >
              <Clapperboard aria-hidden className="size-4" />
              视频生成（M2 未开放）
            </button>
          </div>
        </>
      )}
    </aside>
  );
}

function Tab({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "cursor-pointer rounded-lg border px-2.5 py-1 text-xs transition-colors duration-150",
        active
          ? "border-transparent bg-fg font-medium text-surface"
          : "border-border bg-surface text-fg-muted hover:bg-surface-2",
      )}
    >
      {children}
    </button>
  );
}

/**
 * 一个角色一张卡：缩略图 + 描述 + 出图按钮，底下一条进度。
 *
 * 状态、进度全部来自 `tasks`（ADR-008），跟任务中心看到的是同一个数字。
 */
function CharacterAssetCard({
  card,
  view,
  busy,
  onGenerate,
  onRetry,
}: {
  card: CharacterCard;
  view: RenderView | null;
  busy: boolean;
  onGenerate: () => void;
  onRetry: (taskId: string) => void;
}) {
  const active = view?.status === "queued" || view?.status === "running";
  const failed = view?.status === "failed" || view?.status === "cancelled";
  const done = view?.assetId != null;

  return (
    <div
      className={cn(
        "overflow-hidden rounded-xl border bg-surface",
        active ? "border-running/45" : "border-border",
      )}
    >
      <div className="flex gap-2.5 p-2.5">
        <div
          className={cn(
            "flex h-21 w-16 shrink-0 items-center justify-center overflow-hidden rounded-lg",
            done ? "bg-surface-3" : "bg-surface-2",
          )}
        >
          {view?.assetId ? (
            <RenderThumb assetId={view.assetId} alt={`${card.name} 的基准立绘`} />
          ) : (
            <span className="text-xl font-semibold text-border-strong">{card.glyph}</span>
          )}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="rounded bg-surface-2 px-1.5 py-0.5 text-xs text-fg-muted">
              角色
            </span>
            <span
              className={cn(
                "text-xs",
                done
                  ? "text-success"
                  : failed
                    ? "text-danger"
                    : active
                      ? "animate-pulse-soft text-running"
                      : "text-fg-subtle",
              )}
              title={failed ? (view?.errorCode ?? "") : undefined}
            >
              {done ? "已出图" : failed ? "失败" : active ? "生成中" : "待生成"}
            </span>
            <span className="ml-auto shrink-0 text-xs text-fg-subtle">{card.ref}</span>
          </div>

          <div className="mt-1 truncate text-xs font-semibold text-fg">{card.name}</div>
          <p className="mt-0.5 line-clamp-2 text-xs text-fg-subtle">{card.desc}</p>

          <div className="mt-2 flex items-center gap-1.5">
            <a
              href={`#${groupDomId("characters")}`}
              className="flex h-6.5 items-center rounded-md border border-border-strong px-2 text-xs text-fg-muted transition-colors duration-150 hover:bg-surface-2 hover:text-fg"
            >
              看档案
            </a>
            {failed && view ? (
              <button
                type="button"
                disabled={busy}
                onClick={() => onRetry(view.taskId)}
                className="flex h-6.5 cursor-pointer items-center rounded-md border border-border-strong px-2 text-xs font-medium text-fg transition-colors duration-150 hover:bg-surface-2 disabled:opacity-45"
              >
                {busy ? "提交中…" : "重试"}
              </button>
            ) : (
              <button
                type="button"
                disabled={busy || active}
                onClick={onGenerate}
                // 真实上游调用，会扣 Credits——按钮上说清楚，不要让用户点完才知道
                title={done ? "重新出一张，会再扣一次 Credits" : "真实出图，会扣 Credits"}
                className={cn(
                  "flex h-6.5 cursor-pointer items-center rounded-md px-2 text-xs font-medium transition-colors duration-150 disabled:pointer-events-none disabled:opacity-45",
                  done
                    ? "text-fg-muted hover:bg-surface-2"
                    : "bg-primary text-primary-fg hover:bg-primary-hover",
                )}
              >
                {busy ? "提交中…" : active ? "生成中…" : done ? "重新生成" : "生成图像"}
              </button>
            )}
          </div>
        </div>
      </div>

      {active && view && (
        <div className="h-0.75 bg-surface-2">
          <div
            className="h-full bg-running transition-[width] duration-300"
            style={{ width: `${view.progress}%` }}
          />
        </div>
      )}
    </div>
  );
}
