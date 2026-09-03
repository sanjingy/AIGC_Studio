// 视觉来自 ReelFlow 原型，数据由页面注入
import { Camera, MapPin, MessageSquare, UserRound } from "lucide-react";

import type { ShotCardData } from "./shot-card";
import { ShotImage } from "./shot-image";

/** 参数表里的一格。空值不渲染——留一个 "—" 只是占位噪音。 */
function SpecItem({ label, value, tabular }: { label: string; value?: string | number; tabular?: boolean }) {
  if (value === undefined || value === "") return null;

  return (
    <div className="min-w-0">
      <dt className="text-[10px] text-fg-subtle">{label}</dt>
      <dd className={`mt-1 text-sm break-words text-fg${tabular ? " tnum" : ""}`}>{value}</dd>
    </div>
  );
}

/** 关联档案的胶囊。角色和场景共用同一种形状，区别只在图标。 */
function ProfileChip({ icon, label }: { icon: React.ReactNode; label: string }) {
  return (
    <span className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-border bg-surface-2 px-2.5 py-1 text-[11px] text-fg-muted">
      {icon}
      <span className="min-w-0 truncate">{label}</span>
    </span>
  );
}

/**
 * 单镜详情。`actions` 由页面注入——出图、重做这些动作要花 Credits，
 * 能不能点、点了走哪条接口是页面的事，这里只负责摆位置。
 */
export function ShotDetail(props: {
  shot: ShotCardData & {
    description: string;
    characters: string[];
    scene?: string;
    dialogue?: string;
  };
  actions: React.ReactNode;
}) {
  const { shot, actions } = props;
  const characters = shot.characters ?? [];
  const hasProfiles = characters.length > 0 || Boolean(shot.scene);

  return (
    <article className="overflow-hidden rounded-2xl border border-border bg-surface shadow-rf-card">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4">
        <div className="flex min-w-0 items-center gap-3">
          <span className="shrink-0 font-mono text-xs text-primary">{shot.code}</span>
          <h2 className="truncate text-sm font-semibold text-fg">{shot.title}</h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">{actions}</div>
      </header>

      {/* gap-px + 底色 = 一条分隔线，同时在单列断点下自动消失 */}
      <div className="grid gap-px bg-border lg:grid-cols-[minmax(0,1.15fr)_minmax(15rem,0.85fr)]">
        <div className="min-w-0 bg-surface p-5">
          <div className="relative mb-5 aspect-video overflow-hidden rounded-xl border border-border bg-bg">
            <ShotImage
              src={shot.imageUrl}
              alt={`${shot.code} ${shot.title}`}
              iconClassName="size-10"
            />
          </div>

          <p className="text-[10px] font-semibold tracking-[0.16em] text-fg-subtle uppercase">
            画面描述
          </p>
          <p className="mt-3 text-sm leading-7 break-words text-fg-muted">
            {shot.description || "还没有画面描述。"}
          </p>

          {hasProfiles && (
            <div className="mt-5 flex flex-wrap gap-2" aria-label="关联档案">
              {characters.map((character) => (
                <ProfileChip
                  key={character}
                  icon={<UserRound aria-hidden className="size-3 shrink-0 text-rf-agent" />}
                  label={character}
                />
              ))}
              {shot.scene && (
                <ProfileChip
                  icon={<MapPin aria-hidden className="size-3 shrink-0 text-primary" />}
                  label={shot.scene}
                />
              )}
            </div>
          )}
        </div>

        <div className="min-w-0 bg-surface p-5">
          <div className="flex items-center gap-2 text-[10px] font-semibold tracking-[0.16em] text-fg-subtle uppercase">
            <Camera aria-hidden className="size-3.5 text-primary" />
            镜头参数
          </div>
          <dl className="mt-4 grid grid-cols-2 gap-x-5 gap-y-4">
            <SpecItem label="序号" value={shot.index} tabular />
            <SpecItem label="景别" value={shot.framing} />
            <SpecItem label="运镜" value={shot.camera} />
            <SpecItem label="时长" value={shot.durationLabel} tabular />
          </dl>

          {shot.dialogue && (
            <div className="mt-6 border-t border-border pt-5">
              <p className="flex items-center gap-2 text-[10px] font-semibold tracking-[0.16em] text-fg-subtle uppercase">
                <MessageSquare aria-hidden className="size-3.5 text-rf-agent" />
                台词与声音
              </p>
              <blockquote className="mt-3 border-l-2 border-rf-agent pl-3 text-sm leading-6 break-words text-fg-muted">
                {shot.dialogue}
              </blockquote>
            </div>
          )}
        </div>
      </div>
    </article>
  );
}
