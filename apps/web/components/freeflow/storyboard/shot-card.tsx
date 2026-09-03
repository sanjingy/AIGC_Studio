// 视觉来自 ReelFlow 原型，数据由页面注入
"use client";

import { Check } from "lucide-react";

import { cn } from "@/lib/utils";

import { ShotImage } from "./shot-image";
import { ShotStatus } from "./shot-status";

export interface ShotCardData {
  index: number;
  code: string;
  title: string;
  framing: string;
  camera: string;
  durationLabel?: string;
  status: "ready" | "draft" | "rendering" | "failed";
  imageUrl?: string;
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
      className={cn(
        "group w-full cursor-pointer overflow-hidden rounded-2xl border bg-surface-2 text-left shadow-rf-card",
        "transition-[border-color,background-color,transform] duration-200",
        // 位移只是锦上添花，声明了减少动效就别动
        "hover:-translate-y-0.5 motion-reduce:transform-none motion-reduce:transition-none",
        selected
          ? "border-primary bg-primary-soft"
          : "border-border hover:border-border-strong hover:bg-surface",
      )}
    >
      <div className="relative aspect-video overflow-hidden bg-surface">
        <ShotImage
          src={shot.imageUrl}
          alt={`${shot.code} ${shot.title}`}
          className="transition-transform duration-300 group-hover:scale-[1.02] motion-reduce:transform-none motion-reduce:transition-none"
        />

        <span className="absolute top-3 left-3 max-w-[60%] truncate rounded-full border border-border bg-rf-overlay px-2.5 py-1 font-mono text-[10px] font-medium text-fg backdrop-blur">
          {shot.code}
        </span>
        {shot.durationLabel && (
          <span className="tnum absolute right-3 bottom-3 rounded-md bg-rf-overlay px-2 py-1 font-mono text-[10px] text-fg backdrop-blur">
            {shot.durationLabel}
          </span>
        )}
        {selected && (
          <span className="absolute top-3 right-3 grid size-6 place-items-center rounded-full bg-primary text-primary-fg">
            <Check aria-hidden className="size-3.5" />
            <span className="sr-only">已选择</span>
          </span>
        )}
      </div>

      <div className="space-y-3 p-4">
        <div className="flex items-start gap-3">
          <span className="tnum pt-0.5 font-mono text-[10px] text-fg-subtle">
            {String(shot.index).padStart(2, "0")}
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-fg">{shot.title}</p>
            <p className="mt-1 truncate text-xs text-fg-muted">
              {shot.framing} · {shot.camera}
            </p>
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
      <p className="rounded-2xl border border-dashed border-border px-4 py-12 text-center text-xs text-fg-subtle">
        还没有镜头。先完成分镜，这里会列出每一镜。
      </p>
    );
  }

  return (
    <ul aria-label="镜头列表" className="grid list-none gap-3 sm:grid-cols-2 2xl:grid-cols-3">
      {shots.map((shot, index) => (
        <li key={`${shot.code}-${shot.index}`}>
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
