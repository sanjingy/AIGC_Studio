"use client";

import { useEffect, useState } from "react";
import {
  FileText,
  FileVideo,
  Image as ImageIcon,
  Music,
  Upload,
  Users,
  type LucideIcon,
} from "lucide-react";

import { charactersMeta } from "@/components/project/characters-view";
import { Button } from "@/components/ui/button";
import {
  ApiRequestError,
  assets as assetsApi,
  type CharacterEntry,
  type Library,
  type LibraryAsset,
} from "@/lib/api";
import { ASSET_KIND_LABEL, type AssetKind } from "@/lib/freeflow/types";
import { cn, formatBytes } from "@/lib/utils";

/**
 * 03 素材库（需求文档「屏幕 03」、REQ-030/031）。
 *
 * 数据全部走 `assets.library()`——和 `app/(app)/assets/page.tsx` 是同一个
 * 接口、同一批类型（`LibraryAsset` / `CharacterEntry` / `AssetFolder`），
 * 这一屏只是换了一种组织方式（一级 chip + 卡片网格），没有另造数据结构。
 *
 * 与主线那一页的分工：那页管文件夹归类、从描述建角色、档案展开细读；
 * 这页管「按类型快速找一个素材」。所以这里不重复实现文件夹管理。
 */

/** 用量到这个百分比就开始视觉预警。与 `app/(app)/assets/page.tsx` 同值。 */
const WARN_AT = 90;

/**
 * 一级 chip → `assets.library({type})` 的真实类型参数。
 *
 * `undefined` 表示不带 type 参数，取全量（`all` 以及所有结构化类型都走这条）。
 *
 * 注意一个已知缺口：后端 `apps/api/modules/asset/mime.py` 把
 * `text/plain` / `text/markdown` 映射成资产类型 `text`，而 `AssetKind`
 * （在冻结的 `lib/freeflow/types.ts` 里）没有对应的 chip，所以上传的
 * 小说 txt 目前只在「全部」里出现，「文档」筛不到它。要修得先给
 * AssetKind 加一项，那是公共契约，本轮不动。
 */
const SERVER_TYPE: Record<AssetKind, string | undefined> = {
  all: undefined,
  image: "image",
  video: "video",
  audio: "audio",
  document: "document",
  character: undefined,
  scene: undefined,
  storyboard: undefined,
  workflow: undefined,
  skill: undefined,
};

/**
 * 后端还没有跨类型索引的四类（REQ-030 写明这是待建设施）。
 * 筛出来就是空的——不拿别的东西凑数，凑了用户就会以为索引已经有了。
 */
const UNINDEXED: readonly AssetKind[] = ["scene", "storyboard", "workflow", "skill"];

const KIND_ORDER: readonly AssetKind[] = [
  "all",
  "image",
  "video",
  "audio",
  "character",
  "scene",
  "storyboard",
  "document",
  "workflow",
  "skill",
];

const TYPE_ICON: Record<string, LucideIcon> = {
  image: ImageIcon,
  video: FileVideo,
  audio: Music,
};

/**
 * 二级筛选（REQ-030 里的「项目、角色、场景、标签、时间、类型、模型」）。
 *
 * **只有外观和选中状态，不接真实筛选。** `assets.library()` 只认
 * `type` 和 `folder_id` 两个参数，时间/项目/标签/模型后端一个都没有；
 * 选了之后列表不变是故意的，不是坏了。真要接得先给列表接口加查询参数
 * （标签这一项还得先有标签表）。
 */
const SECONDARY_FILTERS = [
  { id: "time", label: "全部时间", options: ["全部时间", "最近 7 天", "最近 30 天"] },
  { id: "project", label: "全部项目", options: ["全部项目"] },
  { id: "tag", label: "全部标签", options: ["全部标签"] },
  { id: "model", label: "全部模型", options: ["全部模型"] },
] as const;

export function AssetLibraryGrid() {
  const [kind, setKind] = useState<AssetKind>("all");
  const [data, setData] = useState<Library | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    assetsApi
      .library({ type: SERVER_TYPE[kind] })
      .then((d) => {
        if (!alive) return;
        setData(d);
        setError(null);
      })
      .catch((e) => {
        if (!alive) return;
        setError(e instanceof ApiRequestError ? e.error.user_message : "加载资产库失败");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [kind]);

  // 文件：结构化类型的 chip 不展示文件；`all` 和四个文件类 chip 才展示。
  // 后者的过滤已经由后端的 type 参数做完了，前端不再筛一遍。
  const files: LibraryAsset[] =
    data && !UNINDEXED.includes(kind) && kind !== "character" ? data.assets : [];

  // 角色：`characters` 是独立角色档案，后端已经有真实索引。
  const characters: CharacterEntry[] =
    data && (kind === "all" || kind === "character") ? data.characters : [];

  const total = files.length + characters.length;

  return (
    <div className="mx-auto flex max-w-[1200px] flex-col">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h1 className="text-base font-semibold text-fg">我的素材</h1>
        {/* 上传还没接：`lib/api.ts` 里没有上传接口（直传要先建资产记录
            再拿预签名 URL，那条链路前端一行都还没写）。放一个能点的按钮
            比放一个禁用的更糟。 */}
        <Button size="sm" disabled title="原型阶段还没接上传链路">
          <Upload aria-hidden className="size-3.5" />
          上传素材
        </Button>
      </div>

      {data && <UsageLine usage={data.usage} />}

      {/* 一级类型 chip：十项，单选 */}
      <div className="mt-3 flex flex-wrap gap-1.5">
        {KIND_ORDER.map((k) => (
          <button
            key={k}
            type="button"
            aria-pressed={kind === k}
            onClick={() => setKind(k)}
            className={cn(
              "inline-flex h-7 cursor-pointer items-center rounded-full px-2.5 text-xs",
              "transition-colors duration-150",
              kind === k
                ? "bg-primary font-medium text-primary-fg"
                : "bg-surface-2 text-fg-muted hover:bg-surface-3 hover:text-fg",
            )}
          >
            {ASSET_KIND_LABEL[k]}
          </button>
        ))}
      </div>

      <SecondaryFilters />

      {error && (
        <p role="alert" className="mt-3 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}

      {loading && !data ? (
        <p className="mt-6 text-sm text-fg-subtle">加载中…</p>
      ) : UNINDEXED.includes(kind) ? (
        <EmptyState>
          该类型的统一索引还没有接入（REQ-030）。{ASSET_KIND_LABEL[kind]}
          目前是各处的结构化产出，不在 `assets` 这张文件表里，素材库要覆盖它
          得先有一张跨类型的资产索引。
        </EmptyState>
      ) : total === 0 ? (
        <EmptyState>
          {kind === "character"
            ? "还没有独立角色档案。在主线资产库页用一段描述就能生成一份。"
            : "这个类型下还没有素材。出图或上传之后会出现在这里。"}
        </EmptyState>
      ) : (
        <>
          <p className="tnum mt-4 mb-2 text-xs text-fg-subtle">
            {files.length} 个文件
            {characters.length > 0 && ` · ${characters.length} 份角色档案`}
          </p>
          <div className="grid grid-cols-2 items-start gap-3 sm:grid-cols-3 xl:grid-cols-4">
            {files.map((a) => (
              <FileCard key={a.id} asset={a} />
            ))}
            {characters.map((c) => (
              <CharacterCard key={c.id} entry={c} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 容量

/**
 * 容量说明。规则照抄 `app/(app)/assets/page.tsx`（REQ-031 要求维持现实现）：
 * 只有图片/视频/音频这类文件占容量，角色、场景、分镜等结构化产出不占。
 * 百分比用后端算好的 `percent_used`，前端不重算——两处各算一次迟早对不上。
 */
function UsageLine({ usage }: { usage: Library["usage"] }) {
  const { used_bytes, quota_bytes, percent_used } = usage;
  const nearFull = quota_bytes !== null && percent_used >= WARN_AT;

  return (
    <div className="rounded-md border border-border bg-surface px-3 py-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-fg-muted">
          只有图片、视频这类文件占用容量；角色档案与场景档案不计入。
        </span>
        <span className="tnum text-xs text-fg-subtle">
          {quota_bytes === null
            ? `${formatBytes(used_bytes)} · 未设上限`
            : `${formatBytes(used_bytes)} / ${formatBytes(quota_bytes)} · ${percent_used}%`}
        </span>
      </div>
      {quota_bytes !== null && (
        <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-surface-2">
          <div
            role="progressbar"
            aria-valuenow={percent_used}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="资产库已用容量"
            className={cn(
              "h-full transition-[width] duration-300",
              nearFull ? "bg-danger" : "bg-primary",
            )}
            style={{ width: `${Math.min(Math.max(percent_used, used_bytes ? 2 : 0), 100)}%` }}
          />
        </div>
      )}
      {nearFull && (
        <p role="status" className="mt-1.5 text-xs text-danger">
          容量快满了。满了之后新的上传和生成都会被拒绝——删掉不用的素材可以立刻腾出空间。
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 二级筛选

function SecondaryFilters() {
  // 选中值只存在本地：后端没有对应查询参数，选了不会改变列表（见上方注释）
  const [values, setValues] = useState<Record<string, string>>({});

  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5">
      {SECONDARY_FILTERS.map((f) => (
        <select
          key={f.id}
          aria-label={f.label}
          value={values[f.id] ?? f.options[0]}
          onChange={(e) => setValues((v) => ({ ...v, [f.id]: e.target.value }))}
          className="h-7 cursor-pointer rounded-md border border-border-strong bg-surface px-2 text-xs text-fg-muted"
        >
          {f.options.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      ))}
      <span className="text-xs text-fg-subtle">
        二级筛选还没接后端：列表接口只认类型和文件夹，选了不会改变结果。
      </span>
    </div>
  );
}

// ---------------------------------------------------------------- 卡片

function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <p className="mt-6 rounded-md border border-dashed border-border-strong px-4 py-10 text-center text-sm text-fg-subtle">
      {children}
    </p>
  );
}

function FileCard({ asset }: { asset: LibraryAsset }) {
  const [url, setUrl] = useState<string | null>(null);
  const Icon = TYPE_ICON[asset.type] ?? FileText;

  // 缩略图就是原图：真正的缩略图要走一条独立转码链路，现在没有。
  // 链接是预签名的、有有效期，所以只在渲染时现签，不缓存进列表。
  useEffect(() => {
    if (asset.type !== "image") return;
    let alive = true;
    assetsApi
      .downloadUrl(asset.id)
      .then((r) => alive && setUrl(r.url))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [asset.id, asset.type]);

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="flex aspect-square items-center justify-center bg-surface-3">
        {url ? (
          // eslint-disable-next-line @next/next/no-img-element -- 预签名 URL 是运行时才知道的外部地址，用不了 next/image 的构建期优化
          <img src={url} alt={asset.filename} className="size-full object-cover" />
        ) : (
          <Icon aria-hidden className="size-6 text-fg-subtle" />
        )}
      </div>
      <div className="px-2 py-1.5">
        <p className="truncate text-xs text-fg" title={asset.filename}>
          {asset.filename}
        </p>
        <p className="tnum mt-0.5 text-xs text-fg-subtle">
          {asset.size_bytes === null ? "—" : formatBytes(asset.size_bytes)}
          {" · "}
          {new Date(asset.created_at).toLocaleDateString("zh-CN")}
        </p>
      </div>
    </div>
  );
}

function CharacterCard({ entry }: { entry: CharacterEntry }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      {/* 角色档案是结构化文本，没有图。放一个纯色块比放一张假立绘诚实 */}
      <div className="flex aspect-square items-center justify-center bg-surface-3">
        <Users aria-hidden className="size-6 text-fg-subtle" />
      </div>
      <div className="px-2 py-1.5">
        <p className="truncate text-xs text-fg" title={entry.title}>
          {entry.title}
        </p>
        <p className="tnum mt-0.5 text-xs text-fg-subtle">
          {charactersMeta(entry.output)}
          {" · "}
          {new Date(entry.created_at).toLocaleDateString("zh-CN")}
        </p>
      </div>
    </div>
  );
}
