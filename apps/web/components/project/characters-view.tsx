import { RenderSlot } from "@/components/project/render-slot";
import type { Renders } from "@/lib/useRenders";

/**
 * 角色档案产出。外貌字段拼成一行，缺的自动跳过。
 *
 * `renders` 是可选的：资产库那边也用这个组件展示角色档案，但那里的
 * 角色不挂项目，出不了基准立绘（立绘要项目级的风格档案）。
 */
export function CharactersView({ data, renders }: { data: any; renders?: Renders }) {
  return (
    <div className="flex flex-col gap-2 p-3">
      {(data.characters ?? []).map((c: any) => (
        <div key={c.ref} className="flex gap-2.5 rounded-md bg-surface-2 px-2.5 py-2">
          <div className="min-w-0 flex-1">
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

          {renders && c.ref && (
            <RenderSlot
              subject={{ kind: "character", ref: c.ref }}
              renders={renders}
              label="生成基准立绘"
              alt={`${c.name} 的基准立绘`}
              caption="全身基准立绘 · 中性光"
              // 128 而不是 96：次级动作那一格里「查看提示词」「用已有图」
              // 在 96 宽下会各折成两行，一列按钮读起来像四行碎字。
              className="w-32"
            />
          )}
        </div>
      ))}
    </div>
  );
}

export function charactersMeta(data: any): string {
  return `${data.characters?.length ?? 0} 个角色`;
}
