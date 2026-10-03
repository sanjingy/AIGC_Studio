/**
 * 故事 / 剧本工作台的目录、搜索、覆盖关系与写路径。纯函数，不依赖 React 与
 * `@/` 别名，`tests/web-logic.test.mjs` 直接加载它。
 *
 * **身份一律是数组位置**，不是展示编号。字段级编辑的 JSON Pointer 走数组位置
 * （ADR-029 / `apps/api/modules/content/patching.py`），而：
 *
 * - `Episode.index` 是模型写的集号，可以不连续、也可能重复；
 * - `ScriptScene.id`（形如 `1-2`）**跨集可以重复**。
 *
 * 所以一场的身份是 `{ep, at}` 这一对数组下标，`id` 只用于显示与 URL 恢复。
 * 搜索只决定显示哪几行，不改变任何一场的身份——筛完再编辑，改到的仍是原数组
 * 里的那一条。
 *
 * 形状的权威定义在后端 `agents/schemas.py`（Screenplay / Episode / ScriptScene /
 * Beat / NodeCoverage）。这里的类型是它的**只读投影**：每个字段都按"可能没有"
 * 处理，因为库里存着历史形状的产出，而缺字段不能把整页炸掉。
 */

export type BeatKind = "action" | "dialogue" | "vo" | "sfx";

export const BEAT_KINDS: BeatKind[] = ["action", "dialogue", "vo", "sfx"];

export const BEAT_KIND_LABEL: Record<string, string> = {
  action: "动作",
  dialogue: "对白",
  vo: "旁白",
  sfx: "音效",
};

export type BeatLike = {
  kind?: string | null;
  character_ref?: string | null;
  emotion?: string | null;
  text?: string | null;
};

export type SceneLike = {
  id?: string | null;
  location?: string | null;
  time_mood?: string | null;
  character_refs?: string[] | null;
  beats?: BeatLike[] | null;
  hook?: string | null;
};

export type EpisodeLike = {
  index?: number | null;
  title?: string | null;
  scenes?: SceneLike[] | null;
};

export type CoverageLike = {
  node_index?: number | null;
  scene_id?: string | null;
  merged_into?: number | null;
};

export type ScreenplayLike = {
  title?: string | null;
  synopsis?: string | null;
  episodes?: EpisodeLike[] | null;
  node_coverage?: CoverageLike[] | null;
};

/** 一场的身份：两个数组下标。`ep` 是 `episodes` 里的位置，`at` 是该集 `scenes` 里的位置。 */
export type ScenePos = { ep: number; at: number };

/** 主区正在看什么。集与场都用数组位置。 */
export type StoryView =
  | { kind: "story" }
  | { kind: "script" }
  | { kind: "episode"; at: number }
  | { kind: "scene"; ep: number; at: number };

export type SceneRow = {
  /** 该集 `scenes` 数组里的位置 —— 写路径用这个 */
  at: number;
  /** 后端给的场号，只用于显示与 URL；跨集可能重复 */
  id: string;
  location: string;
  timeMood: string;
  characterRefs: string[];
  hook: string;
  beatCount: number;
  dialogueCount: number;
};

export type EpisodeGroup = {
  /** `episodes` 数组里的位置 —— 写路径用这个 */
  at: number;
  /** 后端给的集号，只用于显示；不连续、重复都可能。非法时为 null */
  index: number | null;
  title: string;
  scenes: SceneRow[];
  beatCount: number;
  dialogueCount: number;
};

/* ------------------------------------------------------------------ 基础读取 */

function text(value: unknown): string {
  return typeof value === "string" ? value : value === null || value === undefined ? "" : String(value);
}

function list<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

export function episodesOf(screenplay: ScreenplayLike | null | undefined): EpisodeLike[] {
  return list<EpisodeLike>(screenplay?.episodes);
}

export function scenesOf(episode: EpisodeLike | null | undefined): SceneLike[] {
  return list<SceneLike>(episode?.scenes);
}

export function beatsOf(scene: SceneLike | null | undefined): BeatLike[] {
  return list<BeatLike>(scene?.beats);
}

/** 按数组位置取一场。越界返回 null——返工后集/场都可能变少。 */
export function sceneAt(screenplay: ScreenplayLike | null | undefined, pos: ScenePos): SceneLike | null {
  const episode = episodesOf(screenplay)[pos.ep];
  if (!episode) return null;
  return scenesOf(episode)[pos.at] ?? null;
}

export function episodeAt(
  screenplay: ScreenplayLike | null | undefined,
  at: number,
): EpisodeLike | null {
  return episodesOf(screenplay)[at] ?? null;
}

/** 显示用的集号。模型给的 `index` 非法时退回"第 N 项"的位置序号，不编一个集号。 */
export function episodeLabel(group: { at: number; index: number | null }): string {
  return group.index === null ? `第 ${group.at + 1} 项（集号缺失）` : `第 ${group.index} 集`;
}

export function sceneLabel(row: { at: number; id: string }): string {
  return row.id ? `场 ${row.id}` : `第 ${row.at + 1} 场（场号缺失）`;
}

/* ------------------------------------------------------------------ 目录 */

/**
 * 集 → 场。顺序就是数组顺序，不排序、不去重：数组位置是身份，重排会让
 * "第 2 行"和 `/episodes/1` 指向不同的东西。
 */
export function buildEpisodes(screenplay: ScreenplayLike | null | undefined): EpisodeGroup[] {
  return episodesOf(screenplay).map((episode, at) => {
    const scenes: SceneRow[] = scenesOf(episode).map((scene, sceneAtIndex) => {
      const beats = beatsOf(scene);
      return {
        at: sceneAtIndex,
        id: text(scene?.id).trim(),
        location: text(scene?.location).trim(),
        timeMood: text(scene?.time_mood).trim(),
        characterRefs: list<string>(scene?.character_refs).map((ref) => text(ref)),
        hook: text(scene?.hook).trim(),
        beatCount: beats.length,
        dialogueCount: beats.filter((beat) => beat?.kind === "dialogue" || beat?.kind === "vo").length,
      };
    });
    const rawIndex = Number(episode?.index);
    return {
      at,
      index: Number.isFinite(rawIndex) && rawIndex > 0 ? rawIndex : null,
      title: text(episode?.title).trim(),
      scenes,
      beatCount: scenes.reduce((sum, row) => sum + row.beatCount, 0),
      dialogueCount: scenes.reduce((sum, row) => sum + row.dialogueCount, 0),
    };
  });
}

/** 一场在 URL / React key 上的稳定串。两个数组下标，不含展示编号。 */
export function sceneKey(pos: ScenePos): string {
  return `${pos.ep}:${pos.at}`;
}

export function parseSceneKey(key: string): ScenePos | null {
  const m = /^(\d+):(\d+)$/.exec(key);
  if (!m) return null;
  return { ep: Number(m[1]), at: Number(m[2]) };
}

/* ------------------------------------------------------------------ 搜索 */

/**
 * 一场是否命中搜索词。命中范围：场号、地点、时间氛围、出场角色、钩子、
 * 全部节拍的角色与台词，以及**所在集的集号与标题**——用户搜"第 2 集"
 * 或者集名时，期待的是那一集的场次都在。
 */
export function matchesQuery(
  group: { index: number | null; title: string },
  row: SceneRow,
  scene: SceneLike | null | undefined,
  query: string,
): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const haystack: string[] = [
    row.id,
    row.location,
    row.timeMood,
    row.hook,
    ...row.characterRefs,
    group.title,
    group.index === null ? "" : `第${group.index}集`,
    group.index === null ? "" : `第 ${group.index} 集`,
  ];
  for (const beat of beatsOf(scene)) {
    haystack.push(text(beat?.text), text(beat?.character_ref), text(beat?.emotion));
  }
  return haystack.some((piece) => piece.toLowerCase().includes(q));
}

/**
 * 通过搜索的场，按集、场的数组顺序排好。
 *
 * `pinned` 是**正在编辑的那一场**：它永远在结果里，即使被搜索词筛掉。
 * 筛选不导航，所以草稿不会被筛掉丢掉；但目录里如果连行都不在，用户会
 * 以为它没了。
 */
export function visibleScenes(
  screenplay: ScreenplayLike | null | undefined,
  groups: EpisodeGroup[],
  query: string,
  pinned?: ScenePos | null,
): string[] {
  const out: string[] = [];
  for (const group of groups) {
    for (const row of group.scenes) {
      const pos = { ep: group.at, at: row.at };
      const isPinned = Boolean(pinned && pinned.ep === pos.ep && pinned.at === pos.at);
      if (isPinned || matchesQuery(group, row, sceneAt(screenplay, pos), query)) {
        out.push(sceneKey(pos));
      }
    }
  }
  return out;
}

/* ------------------------------------------------------------------ 覆盖关系 */

export type CoverageInfo = {
  /** 场号 → 落在它上面的情节节点号 */
  byScene: Record<string, number[]>;
  /** 在剧本里出现过一次以上的场号。这些场的覆盖归属无法确定，不猜 */
  duplicatedSceneIds: string[];
  /** `node_coverage` 里有 `scene_id` 的节点数 */
  covered: number;
  /** `node_coverage` 的总条数 */
  total: number;
  /** 被并进别的节点的条数（`merged_into` 有值） */
  merged: number;
};

/**
 * `node_coverage` 的反向索引。
 *
 * 后端只给了 `scene_id`（"1-2"），**没有集号**。同一个场号在多集里出现时，
 * 无法确定这条覆盖指的是哪一集的那一场——那种场号进 `duplicatedSceneIds`，
 * 界面上标"无法确定归属"，绝不挑一集显示。
 */
export function coverageIndex(screenplay: ScreenplayLike | null | undefined): CoverageInfo {
  const byScene: Record<string, number[]> = {};
  let covered = 0;
  let merged = 0;
  const rows = list<CoverageLike>(screenplay?.node_coverage);
  for (const row of rows) {
    const node = Number(row?.node_index);
    if (row?.merged_into !== null && row?.merged_into !== undefined) merged += 1;
    const id = text(row?.scene_id).trim();
    if (!id) continue;
    covered += 1;
    if (!Number.isFinite(node)) continue;
    (byScene[id] ??= []).push(node);
  }

  const seen = new Set<string>();
  const duplicated = new Set<string>();
  for (const episode of episodesOf(screenplay)) {
    for (const scene of scenesOf(episode)) {
      const id = text(scene?.id).trim();
      if (!id) continue;
      if (seen.has(id)) duplicated.add(id);
      seen.add(id);
    }
  }

  return {
    byScene,
    duplicatedSceneIds: [...duplicated],
    covered,
    total: rows.length,
    merged,
  };
}

/* ------------------------------------------------------------------ 统计 */

export type ScreenplayTally = {
  episodes: number;
  scenes: number;
  beats: number;
  dialogue: number;
  covered: number;
  coverageTotal: number;
};

export function tally(
  screenplay: ScreenplayLike | null | undefined,
  groups: EpisodeGroup[],
): ScreenplayTally {
  const coverage = coverageIndex(screenplay);
  return {
    episodes: groups.length,
    scenes: groups.reduce((sum, group) => sum + group.scenes.length, 0),
    beats: groups.reduce((sum, group) => sum + group.beatCount, 0),
    dialogue: groups.reduce((sum, group) => sum + group.dialogueCount, 0),
    covered: coverage.covered,
    coverageTotal: coverage.total,
  };
}

/* ------------------------------------------------------------------ 写路径 */

export type ScreenplayHeadField = "title" | "synopsis";
export type SceneField = "location" | "time_mood" | "character_refs" | "hook";
export type BeatField = "kind" | "character_ref" | "emotion" | "text";

export const SCENE_FIELDS: SceneField[] = ["location", "time_mood", "character_refs", "hook"];
export const BEAT_FIELDS: BeatField[] = ["kind", "character_ref", "emotion", "text"];

export const FIELD_LABEL: Record<string, string> = {
  title: "标题",
  synopsis: "梗概",
  location: "地点",
  time_mood: "时间与氛围",
  character_refs: "出场角色",
  hook: "钩子",
  kind: "类型",
  character_ref: "说话人",
  emotion: "情绪",
  text: "内容",
};

export function headPointer(field: ScreenplayHeadField): string {
  return `/${field}`;
}

export function episodeTitlePointer(at: number): string {
  return `/episodes/${at}/title`;
}

export function scenePointer(pos: ScenePos, field: SceneField): string {
  return `/episodes/${pos.ep}/scenes/${pos.at}/${field}`;
}

export function beatPointer(pos: ScenePos, beatAt: number, field?: BeatField): string {
  const base = `/episodes/${pos.ep}/scenes/${pos.at}/beats/${beatAt}`;
  return field ? `${base}/${field}` : base;
}

/** 这个对象上**确实存在**这个键。后端只 replace 已存在的路径，不新建键。 */
export function hasField(obj: unknown, field: string): boolean {
  return Boolean(obj) && typeof obj === "object" && Object.prototype.hasOwnProperty.call(obj, field);
}

export type BeatDraft = { kind: string; character_ref: string; emotion: string; text: string };

export type SceneDraft = {
  location: string;
  time_mood: string;
  character_refs: string[];
  hook: string;
  beats: BeatDraft[];
};

export function beatDraftOf(beat: BeatLike | null | undefined): BeatDraft {
  return {
    kind: text(beat?.kind) || "action",
    character_ref: text(beat?.character_ref),
    emotion: text(beat?.emotion),
    text: text(beat?.text),
  };
}

export function sceneDraftOf(scene: SceneLike | null | undefined): SceneDraft {
  return {
    location: text(scene?.location),
    time_mood: text(scene?.time_mood),
    character_refs: list<string>(scene?.character_refs).map((ref) => text(ref)),
    hook: text(scene?.hook),
    beats: beatsOf(scene).map(beatDraftOf),
  };
}

/** 这一场哪些字段能编辑。键不在产出里就不能编辑——提交等于让后端新建键，它会拒。 */
export function editableSceneFields(scene: SceneLike | null | undefined): Set<SceneField> {
  const out = new Set<SceneField>();
  for (const field of SCENE_FIELDS) if (hasField(scene, field)) out.add(field);
  return out;
}

/**
 * 一条 beat 是否 4 个键齐全。缺任何一个就走**整条替换**：
 * `/…/beats/{i}` 这个路径本身存在（数组元素在），写进去一个完整合法的 Beat
 * 是允许的；而 `/…/beats/{i}/emotion` 在缺键时不存在，提交必然被拒。
 */
export function beatComplete(beat: BeatLike | null | undefined): boolean {
  return BEAT_FIELDS.every((field) => hasField(beat, field));
}

function sameValue(a: unknown, b: unknown): boolean {
  if (Array.isArray(a) && Array.isArray(b)) {
    // 顺序有意义（出场角色的顺序会进提示词），逐位比
    return a.length === b.length && a.every((v, i) => v === b[i]);
  }
  return a === b;
}

export type PatchDraft = { path: string; value: unknown };

export type ScenePlan = {
  /** 要提交的 patch，一次保存一批 */
  patches: PatchDraft[];
  /** 改了但**提交不了**的字段（产出里没有这个键）。界面上本来就该是禁用的 */
  blocked: SceneField[];
  /** 走了整条替换的 beat 位置，报告与说明用 */
  replacedBeats: number[];
};

/**
 * 把一场的草稿与库里的值对出一批 patch。
 *
 * 只提交**被改过**的字段；缺键的场字段列入 `blocked`（不提交）；缺键的 beat
 * 整条替换。beat 条数不可变，草稿多出或少掉的条一律忽略——新增/删除元素
 * 后端不支持（`patching.py` 第 1 条）。
 */
export function planSceneDraft(
  pos: ScenePos,
  scene: SceneLike | null | undefined,
  draft: SceneDraft,
): ScenePlan {
  const patches: PatchDraft[] = [];
  const blocked: SceneField[] = [];
  const replacedBeats: number[] = [];
  if (!scene) return { patches, blocked, replacedBeats };

  const base = sceneDraftOf(scene);
  for (const field of SCENE_FIELDS) {
    if (sameValue(draft[field], base[field])) continue;
    if (!hasField(scene, field)) {
      blocked.push(field);
      continue;
    }
    patches.push({ path: scenePointer(pos, field), value: draft[field] });
  }

  const beats = beatsOf(scene);
  beats.forEach((beat, beatAt) => {
    const next = draft.beats[beatAt];
    if (!next) return;
    const prev = beatDraftOf(beat);
    const changed = BEAT_FIELDS.filter((field) => !sameValue(next[field], prev[field]));
    if (changed.length === 0) return;
    if (beatComplete(beat)) {
      for (const field of changed) {
        patches.push({ path: beatPointer(pos, beatAt, field), value: next[field] });
      }
      return;
    }
    // 缺键：整条替换。以**原对象**为底再盖上四个字段——原对象上这里不认识的
    // 键（更早或更晚的 schema 带来的）没被用户碰过，不能因为这次替换丢掉。
    replacedBeats.push(beatAt);
    patches.push({
      path: beatPointer(pos, beatAt),
      value: {
        ...(beat && typeof beat === "object" ? beat : {}),
        kind: next.kind,
        character_ref: next.character_ref,
        emotion: next.emotion,
        text: next.text,
      },
    });
  });

  return { patches, blocked, replacedBeats };
}

/* ------------------------------------------------------------------ URL */

/** URL 上本页拥有的键。写 URL 时只动这几个，其他 query 参数原样留着。 */
export const STORY_URL_KEYS = ["view", "ep", "scene", "epAt", "sceneAt"] as const;

export type StoryUrlParams = Record<(typeof STORY_URL_KEYS)[number], string | null>;

function position(raw: string | null, length: number): number | null {
  if (raw === null || !/^\d+$/.test(raw)) return null;
  const at = Number(raw);
  return at < length ? at : null;
}

/**
 * 按"编号 + 位置"找回一集。位置指向的那一集编号对得上（或者 URL 没给编号）
 * 就信位置——重复集号靠它区分；对不上说明产出被返工重排过，退回按编号找
 * 第一个；都没有就是找不到。
 */
function resolveEpisode(groups: EpisodeGroup[], ep: string | null, epAt: string | null): number | null {
  const at = position(epAt, groups.length);
  if (at !== null) {
    const index = groups[at]!.index;
    if (ep === null || (index !== null && String(index) === ep)) return at;
  }
  if (ep === null) return null;
  const found = groups.findIndex((group) => group.index !== null && String(group.index) === ep);
  return found >= 0 ? found : null;
}

function resolveScene(
  group: EpisodeGroup,
  scene: string | null,
  sceneAtRaw: string | null,
): number | null {
  const at = position(sceneAtRaw, group.scenes.length);
  if (at !== null && (scene === null || group.scenes[at]!.id === scene)) return at;
  if (scene === null) return null;
  const found = group.scenes.findIndex((row) => row.id === scene);
  return found >= 0 ? found : null;
}

/**
 * URL → 视图。
 *
 * **query 优先于旧 hash**：只要本页的任一 query 键出现，就完全按 query 解析；
 * 一个都没有时才认旧的 `#plot-index` / `#screenplay`（阶段条 `stageHref` 生成的
 * 那种链接）。两套同时存在又各说各话时，用户看到的会是"点了剧本却停在故事"。
 *
 * 集与场各有两个键：`ep` / `scene` 是**展示编号**（人读得懂、旧链接兼容），
 * `epAt` / `sceneAt` 是**数组位置**（消歧）。集号可以重复、场号跨集与同集都
 * 可能重复，只靠编号刷新会落到第一个同号对象上。解析规则见 `resolveEpisode`。
 *
 * 解析不到就逐级退化 scene → episode → script → story，绝不返回一个指不到
 * 数据的视图。
 */
export function readStoryView(search: string, hash: string, groups: EpisodeGroup[]): StoryView {
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  const get = (key: (typeof STORY_URL_KEYS)[number]) => params.get(key);
  const hasQuery = STORY_URL_KEYS.some((key) => get(key) !== null);

  if (!hasQuery) {
    const anchor = hash.replace(/^#/, "");
    if (anchor === "screenplay") return { kind: "script" };
    return { kind: "story" };
  }

  const wantsEpisode = get("ep") !== null || get("epAt") !== null;
  const wantsScene = get("scene") !== null || get("sceneAt") !== null;

  if (wantsEpisode) {
    const epAt = resolveEpisode(groups, get("ep"), get("epAt"));
    if (epAt !== null) {
      if (wantsScene) {
        const sceneAtIndex = resolveScene(groups[epAt]!, get("scene"), get("sceneAt"));
        if (sceneAtIndex !== null) return { kind: "scene", ep: epAt, at: sceneAtIndex };
      }
      return { kind: "episode", at: epAt };
    }
  }

  const view = get("view");
  if (view === "script") return { kind: "script" };
  if (view === "story") return { kind: "story" };
  // 给了集/场但找不到（返工后集少了、场号改了）：退回完整剧本，
  // 那里能看到现在到底有哪几集
  return wantsEpisode || wantsScene ? { kind: "script" } : { kind: "story" };
}

/**
 * 视图 → 要写进 URL 的键。`null` = 从 URL 上删掉这个键。
 *
 * 集与场**总是**同时写编号与位置：编号给人看、兼容旧链接，位置用来消歧。
 * 编号缺失（模型没给集号或场号）时只写位置，不编一个假编号。
 */
export function storyViewParams(view: StoryView, groups: EpisodeGroup[]): StoryUrlParams {
  const none: StoryUrlParams = { view: null, ep: null, scene: null, epAt: null, sceneAt: null };
  if (view.kind === "story") return none;
  if (view.kind === "script") return { ...none, view: "script" };

  const epAt = view.kind === "episode" ? view.at : view.ep;
  const group = groups[epAt];
  if (!group) return { ...none, view: "script" };
  const episode = {
    ep: group.index === null ? null : String(group.index),
    epAt: String(epAt),
  };
  if (view.kind === "episode") return { ...none, ...episode };

  const row = group.scenes[view.at];
  if (!row) return { ...none, ...episode };
  return { ...none, ...episode, scene: row.id || null, sceneAt: String(view.at) };
}

export function sameStoryView(a: StoryView, b: StoryView): boolean {
  if (a.kind !== b.kind) return false;
  if (a.kind === "episode" && b.kind === "episode") return a.at === b.at;
  if (a.kind === "scene" && b.kind === "scene") return a.ep === b.ep && a.at === b.at;
  return true;
}

/* ------------------------------------------------------------------ 历史 */

/**
 * JSON Pointer → 人话，给改动记录抽屉用。
 * `/episodes/0/scenes/1/beats/2/text` → `第 1 集 · 场 1-2 · 第 3 条节拍 · 内容`。
 * 集号与场号由**数组位置回查真实产出**，不按下标 +1 编号——产出里的集号可能
 * 不连续，编出来的号会指向另一集。
 */
export function describeScreenplayPath(
  screenplay: ScreenplayLike | null | undefined,
  path: string,
): string {
  const head = /^\/(title|synopsis)$/.exec(path);
  if (head) return `剧本${FIELD_LABEL[head[1]!] ?? head[1]!}`;

  const m = /^\/episodes\/(\d+)(?:\/(?:title|scenes\/(\d+)(?:\/(beats\/(\d+)(?:\/(\w+))?|\w+))?))?$/.exec(
    path,
  );
  if (!m) return path;

  const epAt = Number(m[1]);
  const groups = buildEpisodes(screenplay);
  const group = groups[epAt];
  const epText = group ? episodeLabel(group) : `第 ${epAt + 1} 项集`;
  if (path === `/episodes/${epAt}/title`) return `${epText} · 标题`;
  if (m[2] === undefined) return epText;

  const sceneAtIndex = Number(m[2]);
  const row = group?.scenes[sceneAtIndex];
  const sceneText = row ? sceneLabel(row) : `第 ${sceneAtIndex + 1} 场`;
  const tail = m[3];
  if (tail === undefined) return `${epText} · ${sceneText}`;

  if (m[4] !== undefined) {
    const beatNo = Number(m[4]) + 1;
    const field = m[5];
    const beatText = `第 ${beatNo} 条节拍`;
    return field
      ? `${epText} · ${sceneText} · ${beatText} · ${FIELD_LABEL[field] ?? field}`
      : `${epText} · ${sceneText} · ${beatText}（整条）`;
  }
  return `${epText} · ${sceneText} · ${FIELD_LABEL[tail] ?? tail}`;
}
