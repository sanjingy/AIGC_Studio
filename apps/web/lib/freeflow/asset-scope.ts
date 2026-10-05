/**
 * 资产库的筛选、搜索与预览规则。
 *
 * 类型以后端 `apps/api/modules/asset/mime.py` 的 `ALLOWED_MIME` 为准：
 * image / video / audio / text（txt、md）/ workflow（json）/ document（pdf、epub）。
 * 文件类筛选交给 `/assets/library?type=`；档案与 Skill 不是文件，按各自来源列。
 * 不编文件夹、标签、使用次数——后端没有这些可筛的字段。
 */

import type { Library } from "@/lib/api";

export type AssetFilter =
  | "all"
  | "image"
  | "video"
  | "audio"
  | "text"
  | "document"
  | "workflow"
  | "character"
  | "scene"
  | "skill";

export type AssetScope = "global" | "project";

export const FILTER_LABEL: Record<AssetFilter, string> = {
  all: "全部",
  image: "图片",
  video: "视频",
  audio: "音频",
  text: "文本",
  document: "文档",
  workflow: "JSON",
  character: "角色档案",
  scene: "场景档案",
  skill: "Skill",
};

/** chip 的补充说明（`title` 与读屏），写真实的文件格式。 */
export const FILTER_HINT: Partial<Record<AssetFilter, string>> = {
  image: "PNG、JPG、WebP、GIF",
  video: "MP4、WebM、MOV",
  audio: "MP3、WAV、M4A",
  text: "TXT、Markdown",
  document: "PDF、EPUB",
  workflow: "JSON 文件",
};

const FILE_FILTERS = ["image", "video", "audio", "text", "document", "workflow"] as const;

/** 文件类筛选对应的后端 `type` 参数；其余不带 type。 */
export function serverType(filter: AssetFilter): string | undefined {
  return (FILE_FILTERS as readonly string[]).includes(filter) ? filter : undefined;
}

/**
 * 某个范围下可选的筛选。项目范围没有 Skill（它是组织级的，不挂项目）。
 * 独立角色档案同样不挂项目，但「角色档案」筛选在项目范围仍然有项目自己的角色档案。
 */
export function filtersFor(scope: AssetScope): AssetFilter[] {
  const all: AssetFilter[] = ["all", ...FILE_FILTERS, "character", "scene", "skill"];
  return scope === "project" ? all.filter((f) => f !== "skill") : all;
}

export function showsFiles(f: AssetFilter): boolean {
  return f === "all" || serverType(f) !== undefined;
}
export function showsCharacters(f: AssetFilter): boolean {
  return f === "all" || f === "character";
}
export function showsScenes(f: AssetFilter): boolean {
  return f === "all" || f === "scene";
}

/** 空白分词，每个词都要命中（不分大小写）。空查询全部命中。 */
export function matchesQuery(query: string, ...fields: (string | null | undefined)[]): boolean {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (terms.length === 0) return true;
  const hay = fields.filter(Boolean).join("\n").toLowerCase();
  return terms.every((t) => hay.includes(t));
}

/** 档案输出里的对象名称（角色名 / 场景名），供搜索。旧数据缺键时给空。 */
export function profileNames(output: unknown, kind: "characters" | "scenes"): string[] {
  const list = (output as Record<string, unknown> | null)?.[kind];
  if (!Array.isArray(list)) return [];
  return list
    .map((item) => (item && typeof item === "object" ? (item as { name?: unknown }).name : null))
    .filter((n): n is string => typeof n === "string" && n.length > 0);
}

/** 所属项目文案。null = 上传时没挂项目；有 id 却不在列表里的写明不在列表，不显示 uuid。 */
export function sourceLabel(projectId: string | null, titles: ReadonlyMap<string, string>): string {
  if (!projectId) return "未挂项目";
  return titles.get(projectId) ?? "不在项目列表中";
}

export type PreviewKind = "image" | "video" | "audio" | "text" | "none";

/** 文本预览只读这么多字节以内的文件；更大的请下载。 */
export const TEXT_PREVIEW_MAX_BYTES = 1024 * 1024;
/** 文本预览最多展示的字符数。 */
export const TEXT_PREVIEW_CHARS = 20_000;

/**
 * 能在页面里预览的方式。下载地址带 `attachment`，所以 PDF / EPUB / JSON 不内嵌，
 * 只给下载；文本超过上限也只给下载。
 */
export function previewKind(asset: { type: string; size_bytes: number | null }): PreviewKind {
  if (asset.type === "image" || asset.type === "video" || asset.type === "audio") return asset.type;
  if (asset.type === "text") {
    return asset.size_bytes !== null && asset.size_bytes > TEXT_PREVIEW_MAX_BYTES ? "none" : "text";
  }
  return "none";
}

/** 文本预览截断：超出部分不展示，并告诉调用方截了没有。 */
export function clipText(text: string): { text: string; clipped: boolean } {
  const chars = Array.from(text);
  if (chars.length <= TEXT_PREVIEW_CHARS) return { text, clipped: false };
  return { text: chars.slice(0, TEXT_PREVIEW_CHARS).join(""), clipped: true };
}

/** 加载更多时按 id 去重追加：两页边界上同一时刻的资产不重复出现。 */
export function appendPage<T extends { id: string }>(prev: readonly T[], next: readonly T[]): T[] {
  const seen = new Set(prev.map((x) => x.id));
  return [...prev, ...next.filter((x) => !seen.has(x.id))];
}

/** 资产库数据：`usage` 读不到时为 null（不显示容量行），其余列表字段恒为数组。 */
export type LibraryData = Omit<Library, "usage"> & { usage: Library["usage"] | null };

/**
 * 后端 `LibraryOut` 的列表字段恒在；缺了（旧 Mock、代理改写过响应）就当空列表，
 * 只让对应区域显示为空，不让整页抛错。旧的项目素材页一直这样兜底。
 */
export function normalizeLibrary(raw: Partial<Library> | null | undefined): LibraryData {
  const list = <T>(v: T[] | null | undefined): T[] => (Array.isArray(v) ? v : []);
  return {
    usage: raw?.usage ?? null,
    assets: list(raw?.assets),
    next_cursor: typeof raw?.next_cursor === "string" ? raw.next_cursor : null,
    profiles: list(raw?.profiles),
    characters: list(raw?.characters),
    folders: list(raw?.folders),
  };
}
