"use client";

import { Cpu, Laptop } from "lucide-react";

import { cn } from "@/lib/utils";
import type { ImageSource } from "@/lib/api";
import type { LocalRuntimeView } from "@/lib/freeflow/use-local-runtime";

/**
 * 出图来源：平台 API，还是你自己电脑上的 Codex。
 *
 * 三条规则决定了它长这样：
 *
 * 1. **没配就不出现。** 本机运行时是逐项目白名单的试点，没开的项目连
 *    这个控件都不该看到——多一个永远点不了的开关只会让人以为坏了。
 * 2. **配了但用不了，要显示并说明原因**，不能只把它变灰。原因来自后端
 *    （"没有检测到本地连接器，请在你的电脑上启动它"这种），前端不自己编。
 * 3. **两条来源的代价不同，写在控件上**，而且要写准。试点期的口径是：
 *    两条都按同一档 Credits 计费（`billing/pricing.py` 里 `image_source`
 *    根本不参与定价），本机那条**额外**再消耗用户自己的订阅额度。
 *    只写"走你自己的订阅额度"会让人读成"本机不扣 Credits"，那与实际的
 *    资金走向相反。定价规则不在这里改（要改得先定折算规则，见
 *    `15_LOCAL_RUNTIME.md` 的「计费口径」），这里只把话说对。
 */
export function ImageSourcePicker({
  runtime,
  disabled,
  className,
}: {
  runtime: LocalRuntimeView;
  disabled?: boolean;
  className?: string;
}) {
  if (!runtime.configured) return null;

  const options: { value: ImageSource; label: string; hint: string; icon: typeof Cpu }[] = [
    { value: "api", label: "平台", hint: "用平台的模型出图，扣 Credits", icon: Cpu },
    {
      value: "local",
      label: "本机",
      hint: runtime.available
        ? `用你电脑上的 ${runtime.provider ?? "Codex"} 出图：消耗你自己的订阅额度，平台 Credits 仍按同价计费`
        : (runtime.reason ?? "本机生成当前不可用"),
      icon: Laptop,
    },
  ];

  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <div
        role="radiogroup"
        aria-label="出图来源"
        className="flex overflow-hidden rounded border border-border"
      >
        {options.map((option) => {
          const isLocal = option.value === "local";
          const unusable = isLocal && !runtime.available;
          const active = runtime.source === option.value;
          const Icon = option.icon;
          return (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={active}
              disabled={disabled || unusable}
              title={option.hint}
              onClick={() => runtime.setSource(option.value)}
              className={cn(
                "flex flex-1 items-center justify-center gap-1 px-1.5 py-1 text-[11px]",
                "transition-colors duration-150",
                active ? "bg-surface-2 font-medium text-fg" : "text-fg-subtle hover:bg-surface-2",
                (disabled || unusable) && "cursor-not-allowed opacity-50 hover:bg-transparent",
              )}
            >
              <Icon aria-hidden className="size-3 shrink-0" />
              {option.label}
            </button>
          );
        })}
      </div>

      {/* 用不了的时候把原因说出来。只变灰不解释，用户只会看到一个坏掉的开关。 */}
      {!runtime.available && !runtime.loading && (
        <p className="text-[10px] leading-tight text-fg-subtle">{runtime.reason}</p>
      )}
      {runtime.available && runtime.source === "local" && (
        // 试点二字必须留着（ADR-026 同款要求）：它不是正式能力。
        // 后半句是 P1-3 改过的：这条路**额外**消耗用户自己的订阅额度，
        // 平台 Credits 并没有因此变免费——`pricing._shape` 里 image_source
        // 一个字都不参与定价。"额度不足会失败"那句对应 P3-9：
        // 特性开关为 true 不等于这个账号此刻真的能出图。
        <p className="text-[10px] leading-tight text-fg-subtle">
          本机出图（试点）：消耗你电脑上的订阅额度，平台 Credits 仍按同价计费；
          订阅额度不足时这一张会失败（失败不扣 Credits）。图片同样进项目资产库。
        </p>
      )}
    </div>
  );
}
