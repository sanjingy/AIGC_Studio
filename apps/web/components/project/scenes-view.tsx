import { RenderSlot } from "@/components/project/render-slot";
import type { Renders } from "@/lib/useRenders";

/**
 * 场景档案产出。摄影主轴与固定参照物是一致性引擎要用的，别省。
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

          {renders && sc.ref && (
            <RenderSlot
              subject={{ kind: "scene", ref: sc.ref }}
              renders={renders}
              label="生成参考图"
              alt={`${sc.name} 的基准参考图`}
              // 场景是横构图：立绘那套 3/4 竖框会把空间关系压掉，
              // 而空间关系正是这张图存在的理由
              aspect="aspect-[4/3]"
              className="w-32"
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
