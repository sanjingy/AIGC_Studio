import { RenderSlot } from "@/components/project/render-slot";
import { LightingStates, NamedEntryList } from "@/components/project/named-entries";
import {
  defaultLightingOf,
  fixedReferencesOf,
  lightingStatesOf,
} from "@/lib/freeflow/scene-shape";
import type { Renders } from "@/lib/useRenders";

/**
 * 场景档案产出。摄影主轴、固定参照物与光照状态是一致性引擎要用的，别省。
 *
 * 2026-09-07（提交 `2b5a84f`）之后这里有两处形状变了，读法都在
 * `lib/freeflow/scene-shape.ts`：
 *
 *   `fixed_references` 从 `string[]` 变成 `[{name, description, origin}]`
 *   `lighting` 这个字段没有了，改成 `lighting_states[] + default_lighting`
 *
 * 照旧写法渲染出来分别是 `[object Object]` 和 `undefined`。
 *
 * `renders` 是可选的，理由和 `CharactersView` 一字不差：资产库那边也用
 * 这个组件展示场景档案，但那里的场景不挂项目，出不了参考图——参考图要
 * 项目级的风格档案。
 */
export function ScenesView({ data, renders }: { data: any; renders?: Renders }) {
  return (
    <div className="flex flex-col gap-2 p-3">
      {(data.scenes ?? []).map((sc: any) => (
        <div key={sc.ref} className="flex gap-2.5 rounded-md bg-surface-2 px-2.5 py-2">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline gap-2 text-sm">
              <span className="font-medium">{sc.name}</span>
              <span className="text-xs text-fg-subtle">{sc.ref}</span>
              <span className="text-xs text-fg-subtle">{sc.time_slot}</span>
            </div>
            <p className="mt-0.5 text-xs text-fg-muted">{sc.setting}</p>
            {sc.camera_axis && (
              <p className="mt-0.5 text-xs text-fg-subtle">
                摄影主轴：{sc.camera_axis.position} → {sc.camera_axis.facing} →{" "}
                {sc.camera_axis.far_end}
              </p>
            )}

            <div className="mt-1.5 grid gap-2 sm:grid-cols-2">
              <div className="min-w-0">
                <p className="text-[10px] text-fg-subtle">光照状态</p>
                <div className="mt-1">
                  <LightingStates
                    states={lightingStatesOf(sc)}
                    defaultName={defaultLightingOf(sc)}
                  />
                </div>
              </div>
              <div className="min-w-0">
                <p className="text-[10px] text-fg-subtle">固定参照物</p>
                <div className="mt-1">
                  <NamedEntryList
                    entries={fixedReferencesOf(sc)}
                    emptyText="这个场景没有固定参照物"
                  />
                </div>
              </div>
            </div>
          </div>

          {renders && sc.ref && (
            <RenderSlot
              subject={{ kind: "scene", ref: sc.ref }}
              renders={renders}
              label="生成四视图参考图"
              alt={`${sc.name} 的 2×2 四视图参考图：俯视、主轴平视与两个对角机位`}
              // 四视图是一张 2×2 的方图，四个格子每一格都是内容——
              // 立绘那套 3/4 竖框加 object-cover 会把上下两格各切掉一条，
              // 少掉的视角还看不出来。方框 + contain，一格都不裁。
              aspect="aspect-square"
              fit="contain"
              caption="2×2 四视图 · 画面中无人物"
              className="w-36"
            />
          )}
        </div>
      ))}
    </div>
  );
}

export function scenesMeta(data: any): string {
  return `${data.scenes?.length ?? 0} 个场景 · ${data.era ?? ""}`;
}
