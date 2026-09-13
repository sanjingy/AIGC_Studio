"use client";

import { useMemo } from "react";
import { AlertTriangle } from "lucide-react";

import { SceneIcon } from "@/components/icons/studio-icons";
import { NamedEntryList, LightingStates } from "@/components/project/named-entries";
import type { AnchorCard, AnchorsGateSummary } from "@/lib/api";
import {
  defaultLightingOf,
  lightingStatesOf,
  namedEntriesOf,
} from "@/lib/freeflow/scene-shape";
import type { ProjectState } from "@/lib/freeflow/use-project-state";
import { cn } from "@/lib/utils";

import { GateActions } from "./production-actions";

/**
 * 门③ 空间锚点与光照确认（ADR-037 第 3 条 + 2026-09-07 的范围裁决）。
 *
 * **一次性展示全部、一次确认。** ADR-037 明禁逐个确认：场景多的项目会变成
 * 几十次点击，而这些空间关系本来就要放在一起看才判得出来。
 *
 * 这道门一次确认**两样作用域不同的东西**，所以界面上分成两块：
 *
 *   空间锚点固定层  只覆盖**出卡的场景**（`summary.cards`）
 *   光照状态        覆盖**全部场景**，包括走内联描述的
 *
 * 光照没有挂在锚点卡里，是有意的：挂进去的话，走内联描述的场景永远没人
 * 确认过它的光——而它们照样要出图。
 */
export function AnchorsGate({ state }: { state: ProjectState }) {
  const summary = (state.pendingApproval?.payload_json?.summary ?? {}) as AnchorsGateSummary;

  const cards = summary.cards ?? [];
  const inline = summary.inline ?? [];
  const incomplete = new Set(summary.incomplete_refs ?? []);
  const total = summary.scenes_total ?? cards.length + inline.length;

  /**
   * 光照读**场景档案本身**，不读门摘要——`gate_payload` 里没有光照
   * （它管的是锚点），而场景档案就在 `current_state_json` 里，这一页本来
   * 就有。给它另开一条接口只会多一次请求和一次不一致。
   */
  const scenes = useMemo(() => {
    const rows: any[] = Array.isArray(state.output.scenes?.scenes)
      ? state.output.scenes.scenes
      : [];
    return rows
      .filter((scene: any) => typeof scene?.ref === "string")
      .map((scene: any) => ({
        ref: String(scene.ref),
        name: String(scene.name ?? scene.ref),
        states: lightingStatesOf(scene),
        defaultName: defaultLightingOf(scene),
      }));
  }, [state.output.scenes]);

  const cardRefs = useMemo(() => new Set(cards.map((c) => c.ref)), [cards]);

  return (
    <GateActions state={state} gate="anchors">
      <div className="mt-3 flex flex-col gap-3">
        {incomplete.size > 0 && (
          <p
            role="alert"
            className="flex items-start gap-2 rounded-md border border-rf-warning/40 bg-rf-warning-soft px-2.5 py-2 text-xs leading-5 text-rf-warning"
          >
            <AlertTriangle aria-hidden className="mt-px size-3.5 shrink-0" />
            <span>
              <b className="font-semibold">{incomplete.size} 个场景出了卡，但锚点是空的。</b>
              空卡和没有卡对下游是一回事——这一镜的书桌在左边、下一镜在右边，要到成片剪
              在一起才看得出来。确认之前先「打回重做」，在意见里说清哪个场景缺什么。
            </span>
          </p>
        )}

        <Block
          title="空间锚点固定层"
          scope={`${cards.length} / ${total} 个场景要出卡`}
          note="只有满足判据的场景出卡，其余走内联描述。锚点是同一场景所有镜头的空间基准。"
        >
          <CriteriaNote source={summary.criteria_source} />

          {cards.length === 0 ? (
            <p className="mt-2 text-xs text-fg-subtle">
              没有场景需要出锚点卡：每个场景的镜头数、同框人数与位移都在判据之下，全部走内联描述。
            </p>
          ) : (
            <ul className="mt-2 flex flex-col gap-2">
              {cards.map((card) => (
                <AnchorCardRow key={card.ref} card={card} incomplete={incomplete.has(card.ref)} />
              ))}
            </ul>
          )}

          {inline.length > 0 && (
            <div className="mt-2.5 rounded-md border border-border bg-surface-2 px-2.5 py-2">
              <p className="text-xs font-medium text-fg">
                走内联描述、不出卡的 {inline.length} 个场景
              </p>
              <p className="mt-1 flex flex-wrap gap-1.5">
                {inline.map((scene) => (
                  <span
                    key={scene.ref}
                    className="rounded-full bg-surface px-2 py-px text-[11px] text-fg-muted"
                    title={scene.ref}
                  >
                    {scene.name}
                  </span>
                ))}
              </p>
              <p className="mt-1.5 text-xs leading-5 text-fg-subtle">
                它们的空间关系由场景描述直接进提示词，不需要在这里逐个核对。但它们的
                <b className="font-semibold text-fg-muted">光照仍然要确认</b>，见下面一块。
              </p>
            </div>
          )}
        </Block>

        <Block
          title="光照状态"
          scope={`${scenes.length} 个场景，全部`}
          note="每个场景的光照是一个有限集合，镜头从集合里选一个。这里确认的是「有哪几种光」，不是每一镜用哪种。"
        >
          {scenes.length === 0 ? (
            <p className="text-xs text-fg-subtle">还没有场景档案。</p>
          ) : (
            <ul className="mt-2 grid gap-2 lg:grid-cols-2">
              {scenes.map((scene) => (
                <li
                  key={scene.ref}
                  className="rf-grid-card min-w-0 rounded-lg border border-border p-2.5"
                >
                  <div className="flex flex-wrap items-baseline gap-1.5">
                    <SceneIcon aria-hidden className="size-3.5 shrink-0 text-primary" />
                    <span className="text-xs font-semibold text-fg">{scene.name}</span>
                    <span className="font-mono text-[10px] text-fg-subtle">{scene.ref}</span>
                    {cardRefs.has(scene.ref) && (
                      <span className="rounded-full bg-surface-3 px-1.5 py-px text-[10px] text-fg-muted">
                        有锚点卡
                      </span>
                    )}
                  </div>
                  <div className="mt-1.5">
                    <LightingStates states={scene.states} defaultName={scene.defaultName} />
                  </div>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-2 text-xs leading-5 text-fg-subtle">
            少一种光只能重跑场景阶段：字段级编辑只替换已有的值，加不了新的一条，
            所以这里没有「新增」按钮。
          </p>
        </Block>
      </div>
    </GateActions>
  );
}

/**
 * 判据来源的如实说明。
 *
 * 门③ 在分镜**之前**，判据的三个输入却都长在分镜表上，所以后端换成了从
 * 剧本推——算出来的是镜号数的**下界**，不是实测值（`anchors.py` 顶部那段
 * 说明）。界面上必须说清楚，否则用户会把「预计至少 3 个镜号」读成
 * 「这个场景就 3 个镜头」，然后按这个数去估工期和成本。
 */
function CriteriaNote({ source }: { source?: string }) {
  if (source !== "screenplay") return null;
  return (
    <p className="mt-1.5 rounded-md bg-surface-2 px-2.5 py-1.5 text-xs leading-5 text-fg-subtle">
      下面的数字是从<b className="font-semibold text-fg-muted">剧本</b>推出来的
      <b className="font-semibold text-fg-muted">下界</b>（节拍数 → 镜号数的最少值），
      不是分镜实测值。分镜跑完通常比这多，真实镜号数要到「确认分镜」那道门才知道。
    </p>
  );
}

function AnchorCardRow({ card, incomplete }: { card: AnchorCard; incomplete: boolean }) {
  const references = namedEntriesOf(card.fixed_references ?? []);
  const axis = card.camera_axis ?? {};
  const axisText = [axis.position, axis.facing, axis.far_end].filter(Boolean).join(" → ");

  return (
    <li
      className={cn(
        "rf-grid-card min-w-0 rounded-lg border p-2.5",
        incomplete ? "border-rf-warning/45" : "border-border",
      )}
    >
      <div className="flex flex-wrap items-baseline gap-1.5">
        <SceneIcon aria-hidden className="size-3.5 shrink-0 text-primary" />
        <span className="text-sm font-semibold text-fg">{card.name}</span>
        <span className="font-mono text-[10px] text-fg-subtle">{card.ref}</span>
        {incomplete && (
          <span className="rounded-full bg-rf-warning-soft px-1.5 py-px text-[10px] font-medium text-rf-warning">
            锚点为空
          </span>
        )}
      </div>

      {(card.reasons ?? []).length > 0 && (
        <ul className="mt-1.5 flex flex-wrap gap-1.5">
          {(card.reasons ?? []).map((reason) => (
            <li
              key={reason}
              className="rounded-full bg-surface-2 px-2 py-px text-[11px] text-fg-muted"
            >
              {reason}
            </li>
          ))}
        </ul>
      )}

      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        <div className="min-w-0">
          <p className="text-[10px] text-fg-subtle">摄影主轴</p>
          <p className="mt-0.5 text-xs leading-5 text-fg-muted">
            {axisText || <span className="text-rf-warning">没填——同一场景的机位没有基准</span>}
          </p>
        </div>
        <div className="min-w-0">
          <p className="text-[10px] text-fg-subtle">固定参照物（{references.length}）</p>
          <div className="mt-0.5">
            <NamedEntryList
              entries={references}
              emptyText="没填——这个场景没有任何空间锚点"
            />
          </div>
        </div>
      </div>
    </li>
  );
}

/** 门③ 里的一块。标题 + 这一块覆盖多少场景 + 一句它在确认什么。 */
function Block({
  title,
  scope,
  note,
  children,
}: {
  title: string;
  scope: string;
  note: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rf-panel-card rounded-xl border border-border p-3">
      <div className="flex flex-wrap items-baseline gap-2">
        <h4 className="text-sm font-semibold text-fg">{title}</h4>
        <span className="tnum rounded-full bg-surface-2 px-2 py-px text-[11px] text-fg-muted">
          {scope}
        </span>
      </div>
      <p className="mt-1 text-xs leading-5 text-fg-subtle">{note}</p>
      {children}
    </section>
  );
}
