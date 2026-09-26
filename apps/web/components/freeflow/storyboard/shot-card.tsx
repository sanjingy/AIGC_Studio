// 视觉来自 ReelFlow 原型，数据由页面注入
"use client";

import { Check } from "lucide-react";

import { StaleIcon, StoryboardIcon } from "@/components/icons/studio-icons";
import { cn } from "@/lib/utils";

import { ShotImage } from "./shot-image";
import { ShotStatus } from "./shot-status";

export interface ShotCardData {
  index: number;
  code: string;
  title: string;
  framing: string;
  camera: string;
  /**
   * 这一镜用哪种光。**已经解析成显示用的名字**：留空的镜头显示该场景的
   * 默认状态，而不是一片空白——"跟随默认"和"没有光照"在卡片上看起来
   * 一样的话，用户没法一眼扫出哪几镜的光不对。
   */
  lighting?: string;
  /**
   * 这一镜引用的光照状态在该场景里已经不存在了。出图会回落到默认光照，
   * 而后端只记一条 warning——不在卡片上标出来，用户永远不会知道。
   */
  lightingUnknown?: boolean;
  durationLabel?: string;
  status: "ready" | "draft" | "rendering" | "failed";
  imageUrl?: string;
  /**
   * 这一版图出在这一镜最后一次改动之前，可能已经对不上了。
   *
   * **只标记**：不自动重跑、不删旧图（ADR-033 第 4 条）。要不要重出由
   * 用户决定——重出是一次真实的出图调用，会再扣一次 Credits。
   */
  outdated?: boolean;
}

/**
 * 镜头卡。整张卡是一个按钮而不是"卡片里放一颗选择按钮"——命中区越大，
 * 在 1440 屏三列布局下越不容易点空。
 */
export function ShotCard(props: { shot: ShotCardData; selected: boolean; onSelect: () => void }) {
  const { shot, selected, onSelect } = props;

  return (
    <button
      type="button"
      aria-pressed={selected}
      aria-label={`选择镜头 ${shot.code}：${shot.title}`}
      onClick={onSelect}
      data-selected={selected ? "true" : undefined}
      className={cn(
        "ff-shot-frame group w-full cursor-pointer",
        "transition-[border-color] duration-150",
      )}
    >
      <div className="ff-shot-gate">
        <ShotImage src={shot.imageUrl} alt={`${shot.code} ${shot.title}`} />

        <span className="ff-frame-no-burn !bottom-auto !top-1.5">{shot.code}</span>
        {shot.durationLabel && (
          <span className="ff-frame-no-burn !left-auto right-1.5">{shot.durationLabel}</span>
        )}
        {shot.outdated && (
          <span
            title="这张图出在这一镜被改之前，可能与当前内容不符"
            className="absolute bottom-1.5 left-1.5 inline-flex items-center gap-1 rounded-[2px] border border-rf-agent/30 bg-rf-agent-soft px-1.5 py-0.5 text-[10px] text-rf-agent"
          >
            <StaleIcon aria-hidden className="size-2.5" />
            图可能过期
          </span>
        )}
        {selected && (
          <span className="absolute top-1.5 right-1.5 grid size-5 place-items-center rounded-[2px] bg-primary text-primary-fg">
            <Check aria-hidden className="size-3" />
            <span className="sr-only">已选择</span>
          </span>
        )}
      </div>

      <div className="flex flex-col gap-1.5 p-2.5">
        <div className="flex items-start gap-2">
          <span className="ff-shot-no pt-px">{String(shot.index).padStart(2, "0")}</span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-[13px] font-semibold text-fg">{shot.title}</p>
            <p className="mt-0.5 truncate text-[11px] text-fg-muted">
              {shot.framing}／{shot.camera}
            </p>
            {shot.lighting && (
              <p
                title={
                  shot.lightingUnknown
                    ? "这一镜引用的光照状态不在该场景的声明里，出图会回落到默认"
                    : undefined
                }
                className={cn(
                  "mt-0.5 truncate text-[10px]",
                  shot.lightingUnknown ? "text-rf-warning" : "text-fg-subtle",
                )}
              >
                光照：{shot.lighting}
                {shot.lightingUnknown && "（该场景已无此状态）"}
              </p>
            )}
          </div>
        </div>
        <ShotStatus status={shot.status} />
      </div>
    </button>
  );
}

/**
 * 镜头列表。用 `ul/li` 而不是 `role="list"`：原生元素在读屏里更稳，
 * 也不需要额外维护一套 role。
 */
export function ShotGrid(props: {
  shots: ShotCardData[];
  selectedIndex: number | null;
  onSelect: (i: number) => void;
}) {
  const { selectedIndex, onSelect } = props;
  const shots = props.shots ?? [];

  if (shots.length === 0) {
    return (
      <div className="rf-empty-state rounded-[2px] border border-dashed border-border-strong px-4 py-10 text-center">
        <span className="rf-empty-icon"><StoryboardIcon aria-hidden className="size-6" /></span>
        <p className="mt-2 text-xs text-fg-subtle">还没有镜头。先完成分镜，这里会列出每一镜。</p>
      </div>
    );
  }

  return (
    <ul aria-label="镜头接触表" className="ff-contact-sheet list-none">
      {/* key 用列表位置：Agent 不保证镜号唯一，镜号重复时按镜号当 key 会让 React 把两张卡认成一张 */}
      {shots.map((shot, index) => (
        <li key={index}>
          <ShotCard
            shot={shot}
            selected={selectedIndex === index}
            onSelect={() => onSelect(index)}
          />
        </li>
      ))}
    </ul>
  );
}
