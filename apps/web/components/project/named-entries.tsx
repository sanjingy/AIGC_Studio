"use client";

import type { NamedEntry } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 场景档案里两组**具名条目**的展示：固定参照物、光照状态。
 *
 * 两处的数据形状一模一样（`{name, description, origin}`），显示规则也一样，
 * 所以只有这一份组件——门③ 的锚点卡、场景档案详情、资产库里的场景视图
 * 都用它。各写一份的结果是同一条参照物在三个地方长得不一样。
 *
 * **名称加粗、描述次行**，因为这两段是给两个读者看的（后端 `FixedReference`
 * 的说明）：名称是用户扫一眼就能点数的把手，描述是出图模型要复现的细节。
 * 挤成一行，人扫不出这个场景钉了几样东西。
 */

/**
 * `origin === "migrated"` 的角标。
 *
 * 这条必须显示：迁移时名称是**从描述里自动截前 12 个字**截出来的，
 * 不是谁手打的。不标出来，用户会以为那个奇怪的半句话是上一个人写的名字，
 * 然后照着它去改别的地方。
 */
function OriginBadge({ origin }: { origin: NamedEntry["origin"] }) {
  if (origin !== "migrated") return null;
  return (
    <span
      title="这个名称是迁移时从描述里自动截出来的，没有人写过"
      className="shrink-0 rounded-[2px] bg-rf-agent-soft px-1.5 py-px text-[10px] font-medium text-rf-agent"
    >
      自动截取
    </span>
  );
}

export function NamedEntryList({
  entries,
  emptyText,
  className,
}: {
  entries: NamedEntry[];
  /** 一条都没有时说什么。不传就整块不画。 */
  emptyText?: string;
  className?: string;
}) {
  if (entries.length === 0) {
    return emptyText ? <p className="text-xs text-fg-subtle">{emptyText}</p> : null;
  }

  return (
    <ul className={cn("flex flex-col gap-1.5", className)}>
      {entries.map((entry, at) => (
        <li key={`${entry.name}-${at}`} className="min-w-0">
          <div className="flex flex-wrap items-baseline gap-1.5">
            <span className="text-xs font-semibold text-fg">{entry.name}</span>
            <OriginBadge origin={entry.origin} />
          </div>
          {entry.description && entry.description !== entry.name && (
            <p className="mt-0.5 text-xs leading-5 text-fg-muted">{entry.description}</p>
          )}
        </li>
      ))}
    </ul>
  );
}

/**
 * 一个场景的光照状态，渲染成一组标签，默认那个打标记。
 *
 * 光照是**有限集合**而不是一个字段（后端 `LightingState`）：同一个渡口的
 * 清晨、正午、夜巡灯是三种光，镜头从集合里选一个（`StoryboardShot.lighting_ref`），
 * 而不是每镜自己编。所以这里画的是集合，不是一句话。
 *
 * **没有「新增」按钮**：字段级 PATCH 只替换已存在的路径，不新建键也不追加
 * 数组元素（ADR-029 的既有约束），所以「加一个光照状态」前端做不到，
 * 只能重跑场景阶段。摆一个加不了的按钮就是假入口。
 */
export function LightingStates({
  states,
  defaultName,
  className,
}: {
  states: NamedEntry[];
  /** 没写 `lighting_ref` 的镜头用哪一个。 */
  defaultName: string;
  className?: string;
}) {
  if (states.length === 0) {
    return <p className="text-xs text-fg-subtle">这个场景还没有光照状态。</p>;
  }

  return (
    <ul className={cn("flex flex-col gap-1.5", className)}>
      {states.map((state, at) => {
        const isDefault = state.name === defaultName;
        return (
          <li
            key={`${state.name}-${at}`}
            className={cn(
              "min-w-0 rounded-md border px-2 py-1.5",
              isDefault ? "border-primary/35 bg-primary-soft" : "border-border bg-surface-2",
            )}
          >
            <div className="flex flex-wrap items-baseline gap-1.5">
              <span
                className={cn(
                  "text-xs font-semibold",
                  isDefault ? "text-primary" : "text-fg",
                )}
              >
                {state.name}
              </span>
              {isDefault && (
                <span
                  className="shrink-0 rounded-[2px] bg-primary/15 px-1.5 py-px text-[10px] font-medium text-primary"
                  title="镜头没写光照时用这一个"
                >
                  默认
                </span>
              )}
              <OriginBadge origin={state.origin} />
            </div>
            {state.description && (
              <p className="mt-0.5 text-xs leading-5 text-fg-muted">{state.description}</p>
            )}
          </li>
        );
      })}
    </ul>
  );
}
