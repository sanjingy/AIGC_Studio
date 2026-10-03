/**
 * 角色 / 场景工作台（Reelbench P2B）的纯逻辑。
 *
 * 不依赖 React 和 `@/` 别名，`node --test` 直接加载（见 `tests/web-logic.test.mjs`）。
 *
 * 两条铁律与剧本页相同：
 *
 * 1. **身份是数组位置。** 目录显示名称和 ref，但 JSON Pointer 一律是
 *    `/characters/{at}/…`、`/scenes/{at}/…`。搜索只决定显示，不改变身份。
 * 2. **只写已经存在的路径。** 后端 `patching.write_at` 不新建键，旧产出缺的
 *    键在这里就标成只读；需要整项替换的具名条目，替换值带齐原对象的其他键。
 */

export type Role = "characters" | "scenes";

type Row = Record<string, unknown>;

export type PatchOp = { path: string; value: unknown };

function isRow(value: unknown): value is Row {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function text(value: unknown): string {
  return value === null || value === undefined ? "" : String(value).trim();
}

function has(row: unknown, key: string): boolean {
  return isRow(row) && Object.prototype.hasOwnProperty.call(row, key);
}

function listOf(value: unknown): string[] {
  return Array.isArray(value) ? value.map((v) => text(v)).filter(Boolean) : [];
}

/* ------------------------------------------------------------ 受控词表 */

/**
 * 抄自 `agents/schemas.py` 的 Literal。测试会解析那个文件逐项比对，
 * 词表一改测试就红——前端没有接口能拿到它们，只能靠这道闸防漂移。
 */
export const BEING_KINDS = ["人类", "动物", "怪物", "神兽", "异形", "机械生命"] as const;
export const CAMPS = ["正派", "反派", "中立", "未知"] as const;
export const HEIGHT_BANDS = ["娇小", "偏矮", "中等身高", "偏高", "高挑", "魁梧高大"] as const;
export const BODY_TYPES = [
  "瘦削嶙峋",
  "纤细单薄",
  "精瘦结实",
  "健硕壮实",
  "宽肩厚背",
  "圆润丰腴",
  "肥硕臃肿",
] as const;
export const POSTURES = [
  "挺拔端正",
  "含胸驼背",
  "松弛慵懒",
  "紧绷戒备",
  "佝偻蜷缩",
  "轻盈灵动",
  "沉稳压场",
] as const;

/* ------------------------------------------------------------ 目录行 */

export type ProfileRow = {
  /** 在 `characters[]` / `scenes[]` 里的位置——唯一身份 */
  at: number;
  ref: string;
  name: string;
  /** 目录第二行：角色的身份 / 场景的时段 */
  subtitle: string;
  /** 搜索用的拼接文本（小写） */
  haystack: string;
};

export function itemsOf(block: unknown, role: Role): unknown[] {
  const list = isRow(block) ? block[role] : null;
  return Array.isArray(list) ? list : [];
}

export function buildRows(block: unknown, role: Role): ProfileRow[] {
  return itemsOf(block, role).map((item, at) => {
    const row = isRow(item) ? item : {};
    const ref = text(row.ref);
    const name = text(row.name);
    const parts =
      role === "characters"
        ? [ref, name, row.identity, row.relations, row.camp, row.kind, row.present_state]
        : [ref, name, row.time_slot, row.setting, ...listOf(row.key_elements)];
    return {
      at,
      ref,
      name,
      subtitle: role === "characters" ? text(row.identity) : text(row.time_slot),
      haystack: parts.map(text).join("\n").toLowerCase(),
    };
  });
}

/** 通过搜索的位置。`pinned`（正在编辑的那一个）不管匹不匹配都留着。 */
export function visibleRows(rows: ProfileRow[], query: string, pinned: number | null): number[] {
  const q = query.trim().toLowerCase();
  return rows.filter((r) => !q || r.haystack.includes(q) || r.at === pinned).map((r) => r.at);
}

/* ------------------------------------------------------------ 视图与 URL */

export type ProfileView = { kind: "overview" } | { kind: "item"; at: number };

function duplicated(rows: ProfileRow[], ref: string): boolean {
  return rows.filter((r) => r.ref === ref).length > 1;
}

/** `?ref=` 定位；ref 重复时 `&at=` 消歧，位置与 ref 对不上时信 ref。找不到退回总览。 */
export function readProfileView(search: string, rows: ProfileRow[]): ProfileView {
  const params = new URLSearchParams(search);
  const ref = params.get("ref");
  if (!ref) return { kind: "overview" };
  const at = Number(params.get("at"));
  if (Number.isInteger(at) && rows[at]?.ref === ref) return { kind: "item", at };
  const hit = rows.find((r) => r.ref === ref);
  return hit ? { kind: "item", at: hit.at } : { kind: "overview" };
}

export function profileViewParams(
  view: ProfileView,
  rows: ProfileRow[],
): { ref: string | null; at: string | null } {
  if (view.kind !== "item") return { ref: null, at: null };
  const row = rows[view.at];
  if (!row?.ref) return { ref: null, at: null };
  return { ref: row.ref, at: duplicated(rows, row.ref) ? String(view.at) : null };
}

/* ------------------------------------------------------------ 字段 */

export type FieldControl = "text" | "multiline" | "select" | "list";

export type FieldSpec = {
  /** 对象内稳定的键，也是草稿的键 */
  id: string;
  label: string;
  group: string;
  control: FieldControl;
  /** 库里现在的值（列表字段是数组） */
  value: string | string[];
  options?: readonly string[];
  maxLength?: number;
  /** 不能编辑的原因；null = 可编辑 */
  blocked: string | null;
  hint?: string;
  /** 草稿值 → 这个字段的补丁。`blocked` 不为 null 时不会被调用 */
  toPatch: (next: string | string[]) => PatchOp;
};

export const MISSING_KEY = "旧档案没有这一项，只能整段返工补上";

function scalarField(
  base: string,
  row: unknown,
  key: string,
  label: string,
  group: string,
  opts: { control?: FieldControl; maxLength?: number; options?: readonly string[]; hint?: string } = {},
): FieldSpec {
  const control = opts.control ?? "text";
  const present = has(row, key);
  const raw = present ? (row as Row)[key] : undefined;
  const value = control === "list" ? listOf(raw) : text(raw);
  let blocked = present ? null : MISSING_KEY;
  if (!blocked && control === "select" && opts.options && !opts.options.includes(value as string)) {
    blocked = "当前值不在受控词表里，只能整段返工修正";
  }
  if (!blocked && control === "list" && raw !== undefined && !Array.isArray(raw)) {
    blocked = "这一项的旧格式不是列表，只能整段返工修正";
  }
  return {
    id: key,
    label,
    group,
    control,
    value,
    options: opts.options,
    maxLength: opts.maxLength,
    hint: opts.hint,
    blocked,
    toPatch: (next) => ({ path: `${base}/${key}`, value: next }),
  };
}

const CHARACTER_TEXT: [key: string, label: string, group: string, max: number, control?: FieldControl][] = [
  ["name", "名称", "身份", 40],
  ["identity", "身份", "身份", 120],
  ["relations", "人物关系", "身份", 200, "multiline"],
  ["power_position", "权力位置", "身份", 40],
  ["arc_stage", "成长阶段", "身份", 40],
  ["present_state", "当前状态", "身份", 120],
  ["age_range", "年龄段", "外貌", 20],
  ["hair", "发型", "外貌", 80],
  ["eyes", "眼睛", "外貌", 60],
  ["face", "面部", "外貌", 100],
  ["outfit", "服装", "外貌", 160, "multiline"],
  ["distinctive", "显著特征", "外貌", 120],
  ["nationality", "国籍", "外貌", 40],
  ["ethnicity", "人种", "外貌", 40],
  ["skin", "肤色", "外貌", 60],
  ["shoes", "鞋子", "外貌", 80],
  ["accessories", "配饰", "外貌", 120],
];

export function characterFields(item: unknown, at: number): FieldSpec[] {
  const base = `/characters/${at}`;
  const out: FieldSpec[] = [];
  for (const [key, label, group, max, control] of CHARACTER_TEXT.slice(0, 1)) {
    out.push(scalarField(base, item, key, label, group, { maxLength: max, control }));
  }
  out.push(scalarField(base, item, "kind", "类别", "身份", { control: "select", options: BEING_KINDS }));
  out.push(scalarField(base, item, "camp", "阵营", "身份", { control: "select", options: CAMPS }));
  for (const [key, label, group, max, control] of CHARACTER_TEXT.slice(1, 3)) {
    out.push(scalarField(base, item, key, label, group, { maxLength: max, control }));
  }
  out.push(
    scalarField(base, item, "personality", "性格", "身份", {
      control: "list",
      hint: "每行一项，1–4 项",
    }),
  );
  for (const [key, label, group, max, control] of CHARACTER_TEXT.slice(3)) {
    out.push(scalarField(base, item, key, label, group, { maxLength: max, control }));
  }
  out.push(scalarField(base, item, "height", "身高", "体貌词表", { control: "select", options: HEIGHT_BANDS }));
  out.push(scalarField(base, item, "body_type", "体型", "体貌词表", { control: "select", options: BODY_TYPES }));
  out.push(scalarField(base, item, "posture", "体态", "体貌词表", { control: "select", options: POSTURES }));
  return out;
}

/* ------------------------------------------------------------ 场景的具名条目 */

/** 与后端 `MIGRATED_NAME_CHARS` / `DEFAULT_LIGHTING_NAME` 同值 */
const DERIVED_NAME_CHARS = 12;
export const DEFAULT_LIGHTING_NAME = "默认";

export type NamedItem = {
  /** 原始数组里的位置 */
  index: number;
  name: string;
  description: string;
  origin: "authored" | "migrated";
};

/**
 * 按**原始下标**读具名条目。与 `scene-shape.ts` 的归一化规则一致，
 * 区别是保留原始位置——写回必须打在原始那一格上。
 *
 * `dropped`：原始列表里有会被后端规范化丢掉的项（空项；光照还有重名）。
 * 后端写入后按下标回读规范化结果，丢项会让下标错位、把别的条目写进这一格，
 * 所以这样的列表整组只读。
 */
export function namedItems(
  value: unknown,
  dedupe: boolean,
): { items: NamedItem[]; dropped: boolean } {
  if (!Array.isArray(value)) return { items: [], dropped: false };
  const items: NamedItem[] = [];
  const seen = new Set<string>();
  let dropped = false;
  value.forEach((raw, index) => {
    let name: string;
    let description: string;
    let origin: string;
    if (isRow(raw)) {
      name = text(raw.name);
      description = text(raw.description);
      origin = text(raw.origin);
      if (!name && !description) {
        dropped = true;
        return;
      }
      if (!description) {
        description = name;
        origin ||= "migrated";
      }
      if (!name) {
        name = description.slice(0, DERIVED_NAME_CHARS);
        origin ||= "migrated";
      }
    } else {
      description = text(raw);
      if (!description) {
        dropped = true;
        return;
      }
      name = description.slice(0, DERIVED_NAME_CHARS);
      origin = "migrated";
    }
    if (dedupe) {
      if (seen.has(name)) {
        dropped = true;
        return;
      }
      seen.add(name);
    }
    items.push({ index, name, description, origin: origin === "migrated" ? "migrated" : "authored" });
  });
  return { items, dropped };
}

/**
 * 改一条具名条目的描述。**名称永远保持现在显示的那个**：光照名被分镜
 * `lighting_ref` 引用，参照物名是界面与下游的把手；让后端按新描述重新截名
 * 会静默断掉引用。
 */
export function namedItemPatch(listPath: string, raw: unknown, item: NamedItem, next: string): PatchOp {
  if (isRow(raw) && text(raw.name) && has(raw, "description")) {
    return { path: `${listPath}/${item.index}/description`, value: next };
  }
  const value: Row = isRow(raw) ? { ...raw } : {};
  value.name = item.name;
  value.description = next;
  if (!has(value, "origin")) value.origin = item.origin;
  return { path: `${listPath}/${item.index}`, value };
}

export type SceneLighting = {
  items: NamedItem[];
  /** 旧扁平 `lighting` 字符串（且没有 `lighting_states`） */
  legacy: boolean;
  defaultName: string;
  dropped: boolean;
};

export function sceneLighting(scene: unknown): SceneLighting {
  const row = isRow(scene) ? scene : {};
  const { items, dropped } = namedItems(row.lighting_states, true);
  if (items.length > 0 || dropped) {
    const declared = text(row.default_lighting);
    const defaultName = items.some((i) => i.name === declared) ? declared : (items[0]?.name ?? "");
    return { items, legacy: false, defaultName, dropped };
  }
  if (has(row, "lighting")) {
    return {
      items: [{ index: -1, name: DEFAULT_LIGHTING_NAME, description: text(row.lighting), origin: "migrated" }],
      legacy: true,
      defaultName: DEFAULT_LIGHTING_NAME,
      dropped: false,
    };
  }
  return { items: [], legacy: false, defaultName: "", dropped: false };
}

const LIST_DROPPED = "这一组里有空项或重名，逐条改会写错位置，只能整段返工";

export function sceneFields(item: unknown, at: number): FieldSpec[] {
  const base = `/scenes/${at}`;
  const row = isRow(item) ? item : {};
  const out: FieldSpec[] = [
    scalarField(base, row, "name", "名称", "场景", { maxLength: 40 }),
    scalarField(base, row, "time_slot", "时段", "场景", { maxLength: 20 }),
    scalarField(base, row, "setting", "环境描述", "场景", { control: "multiline", maxLength: 300 }),
    scalarField(base, row, "key_elements", "关键元素", "场景", { control: "list", hint: "每行一项，1–12 项" }),
  ];

  const axis = row.camera_axis;
  for (const [key, label] of [
    ["position", "机位"],
    ["facing", "朝向"],
    ["far_end", "远景末端"],
  ] as const) {
    const field = scalarField(`${base}/camera_axis`, axis, key, label, "摄影主轴", { maxLength: 80 });
    field.id = `camera_axis.${key}`;
    if (!isRow(axis)) field.blocked = MISSING_KEY;
    out.push(field);
  }

  const lighting = sceneLighting(row);
  const rawStates = Array.isArray(row.lighting_states) ? row.lighting_states : [];
  for (const state of lighting.items) {
    out.push({
      id: `lighting.${state.index}`,
      label: state.name,
      group: "光照状态",
      control: "text",
      value: state.description,
      maxLength: 100,
      hint: state.origin === "migrated" ? "名称由旧数据自动生成" : undefined,
      blocked: lighting.dropped ? LIST_DROPPED : null,
      toPatch: (next) =>
        lighting.legacy
          ? { path: `${base}/lighting`, value: next }
          : namedItemPatch(`${base}/lighting_states`, rawStates[state.index], state, next as string),
    });
  }
  if (lighting.items.length > 0) {
    const field = scalarField(base, row, "default_lighting", "默认光照", "光照状态", {
      control: "select",
      options: lighting.items.map((i) => i.name),
      hint: "没指定光照的镜头用它",
    });
    field.value = lighting.defaultName;
    if (lighting.legacy || lighting.dropped) field.blocked = lighting.dropped ? LIST_DROPPED : MISSING_KEY;
    else if (has(row, "default_lighting")) field.blocked = null;
    out.push(field);
  }

  const refs = namedItems(row.fixed_references, false);
  const rawRefs = Array.isArray(row.fixed_references) ? row.fixed_references : [];
  for (const ref of refs.items) {
    out.push({
      id: `fixed.${ref.index}`,
      label: ref.name,
      group: "固定参照物",
      control: "multiline",
      value: ref.description,
      maxLength: 160,
      hint: ref.origin === "migrated" ? "名称由旧数据自动生成" : undefined,
      blocked: refs.dropped ? LIST_DROPPED : null,
      toPatch: (next) => namedItemPatch(`${base}/fixed_references`, rawRefs[ref.index], ref, next as string),
    });
  }
  return out;
}

/* ------------------------------------------------------------ 草稿 → 补丁 */

export type Draft = Record<string, string | string[]>;

/** 列表字段：每行一项，去空白、去空行 */
export function parseList(textValue: string): string[] {
  return textValue
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
}

export function sameValue(a: string | string[], b: string | string[]): boolean {
  if (Array.isArray(a) || Array.isArray(b)) {
    const x = Array.isArray(a) ? a : [a];
    const y = Array.isArray(b) ? b : [b];
    return x.length === y.length && x.every((v, i) => v === y[i]);
  }
  return a === b;
}

export function dirtyFields(fields: FieldSpec[], draft: Draft): FieldSpec[] {
  return fields.filter(
    (f) => f.blocked === null && draft[f.id] !== undefined && !sameValue(draft[f.id]!, f.value),
  );
}

/** 库值变化后草稿怎么跟，三个编辑器共用一份，见 `draft-rebase.ts` */
export { rebaseDraft } from "./draft-rebase";

export function planPatches(fields: FieldSpec[], draft: Draft): PatchOp[] {
  return dirtyFields(fields, draft).map((f) => f.toPatch(draft[f.id]!));
}

/* ------------------------------------------------------------ 历史路径 */

const CHARACTER_LABEL: Record<string, string> = {
  kind: "类别",
  camp: "阵营",
  personality: "性格",
  height: "身高",
  body_type: "体型",
  posture: "体态",
  build: "旧体型描述",
  ...Object.fromEntries(CHARACTER_TEXT.map(([key, label]) => [key, label])),
};

const SCENE_LABEL: Record<string, string> = {
  name: "名称",
  time_slot: "时段",
  setting: "环境描述",
  key_elements: "关键元素",
  camera_axis: "摄影主轴",
  position: "机位",
  facing: "朝向",
  far_end: "远景末端",
  lighting: "光照",
  lighting_states: "光照状态",
  default_lighting: "默认光照",
  fixed_references: "固定参照物",
  description: "描述",
};

/** `/scenes/2/lighting_states/1/description` → 「渡口 · 光照状态 夜灯 · 描述」 */
export function describeProfilePath(block: unknown, role: Role, path: string): string {
  const parts = path.split("/").slice(1);
  if (parts[0] !== role) return path;
  const at = Number(parts[1]);
  const item = itemsOf(block, role)[at];
  const who = (isRow(item) && (text(item.name) || text(item.ref))) || `第 ${at + 1} 个`;
  const labels = role === "characters" ? CHARACTER_LABEL : SCENE_LABEL;
  const rest: string[] = [];
  for (let i = 2; i < parts.length; i++) {
    const token = parts[i]!;
    const prev = parts[i - 1];
    if (/^\d+$/.test(token) && (prev === "lighting_states" || prev === "fixed_references") && isRow(item)) {
      const list = item[prev];
      const entry = Array.isArray(list) ? list[Number(token)] : undefined;
      const name = isRow(entry) ? text(entry.name) : text(entry).slice(0, DERIVED_NAME_CHARS);
      rest[rest.length - 1] = `${rest[rest.length - 1]} ${name || `第 ${Number(token) + 1} 条`}`;
      continue;
    }
    rest.push(labels[token] ?? token);
  }
  return [who, ...rest].join(" · ");
}
