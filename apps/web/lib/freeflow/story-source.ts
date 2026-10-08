/**
 * 故事原文（`current_state_json.source`）的纯逻辑：导入文件的解码与校验、
 * 保存前校验、本地草稿的键与读写。
 *
 * 不依赖 React 与 `@/` 别名，`tests/web-logic.test.mjs` 直接加载。
 *
 * 文件只在浏览器里读成文本放进输入框，**不上传**：原文的唯一去处是
 * `PUT /projects/{id}/source`，与粘贴走同一条路。
 */
import type { ProjectStateSnapshot, SourceSaved } from "../api";
import { SOURCE_MAX, charCount } from "./home-start";

export { SOURCE_MAX, charCount };

/** react-dropzone 的 `accept`。只认纯文本：.docx 是 zip 包，读成文本是一堆乱码。 */
export const SOURCE_FILE_ACCEPT: Record<string, string[]> = {
  "text/plain": [".txt"],
  "text/markdown": [".md", ".markdown"],
};

/**
 * 文件大小上限。2 万字在 UTF-8 下最多 8 万字节（4 字节字符），GBK 更小；
 * 给 1 MB 是为了让"字数超了"能报出准确字数，而不是笼统一句"文件太大"。
 */
export const SOURCE_FILE_MAX_BYTES = 1024 * 1024;

export type DecodeResult =
  | { ok: true; text: string; encoding: "utf-8" | "utf-16le" | "utf-16be" | "gb18030" }
  | { ok: false; error: string };

/**
 * 把文件字节解成文本。顺序：BOM → 严格 UTF-8 → GB18030（中文 txt 小说的常见编码）。
 *
 * 不用编码探测库：这里只有"是不是 UTF-8"一个分叉，严格解码失败就是答案
 * （调研见 orca/tasks/USER_FLOW_REPAIR/reuse.md）。解出来夹着大量控制字符的，
 * 判为不是纯文本——改了扩展名的二进制文件会走到这里。
 */
export function decodeStoryFile(bytes: Uint8Array): DecodeResult {
  if (bytes.length === 0) return { ok: false, error: "文件是空的" };

  let encoding: "utf-8" | "utf-16le" | "utf-16be" | "gb18030";
  let text: string;
  if (bytes[0] === 0xff && bytes[1] === 0xfe) {
    encoding = "utf-16le";
    text = new TextDecoder("utf-16le").decode(bytes.subarray(2));
  } else if (bytes[0] === 0xfe && bytes[1] === 0xff) {
    encoding = "utf-16be";
    text = new TextDecoder("utf-16be").decode(bytes.subarray(2));
  } else {
    try {
      // TextDecoder 默认会吃掉 UTF-8 BOM
      text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      encoding = "utf-8";
    } catch {
      try {
        text = new TextDecoder("gb18030", { fatal: true }).decode(bytes);
        encoding = "gb18030";
      } catch {
        return { ok: false, error: "读不出文字：既不是 UTF-8 也不是 GBK 编码。用记事本另存为 UTF-8 后再导入" };
      }
    }
  }

  if (looksBinary(text)) {
    return { ok: false, error: "这不是纯文本文件。Word、PDF 请先另存为 .txt 再导入" };
  }
  text = text.replace(/\r\n?/g, "\n");
  if (!text.trim()) return { ok: false, error: "文件里没有文字" };
  return { ok: true, text, encoding };
}

/** NUL 或控制字符（制表、换行、换页除外）超过 1% 就不是人写的文本。 */
function looksBinary(text: string): boolean {
  if (text.includes("\u0000")) return true;
  const sample = text.slice(0, 4096);
  let control = 0;
  for (const ch of sample) {
    const code = ch.charCodeAt(0);
    if (code < 0x20 && ch !== "\n" && ch !== "\r" && ch !== "\t" && ch !== "\f") control += 1;
  }
  return sample.length > 0 && control / sample.length > 0.01;
}

/** react-dropzone 拒收的原因码 → 人话。未知码照实给原文，不吞。 */
export function fileRejectionText(code: string, fallback: string): string {
  switch (code) {
    case "file-invalid-type":
      return "只支持 .txt 和 .md 纯文本文件。Word（.docx）、PDF 请先另存为 .txt";
    case "file-too-large":
      return "文件超过 1 MB。原文上限是 2 万字，远小于这个大小，请确认选对了文件";
    case "too-many-files":
      return "一次只能导入一个文件";
    default:
      return fallback || "这个文件不能导入";
  }
}

/** 保存 / 开始生产前的校验。null = 可以提交。 */
export function sourceProblem(text: string): string | null {
  const t = text.trim();
  if (!t) return "原文是空的，先粘贴或导入小说原文、创意";
  const n = charCount(t);
  if (n > SOURCE_MAX) {
    return `原文 ${n.toLocaleString("zh-CN")} 字，超过 ${SOURCE_MAX.toLocaleString("zh-CN")} 字上限，删减后再保存`;
  }
  return null;
}

/** 草稿与已保存的原文有没有实质差别（首尾空白不算，后端存的是 strip 之后的）。 */
export function sourceDirty(draft: string, saved: string): boolean {
  return draft.trim() !== saved.trim();
}

// ---------------------------------------------------------------- 本地草稿

/**
 * 本地草稿的键：账号 + 项目两级隔离。只按项目分，同一台电脑换账号登录会看到
 * 上一个人的稿子；只按账号分，两个项目的稿子会互相覆盖。
 */
export function draftKey(userId: string, projectId: string): string {
  return `aigc.story-source-draft.v1:${userId}:${projectId}`;
}

export type SourceDraft = { text: string; savedAt: string };

type DraftStore = Pick<Storage, "getItem" | "setItem" | "removeItem">;

/** 读草稿。存储不可用、内容损坏都当作没有草稿——草稿只是保险，不能让页面因它出错。 */
export function readDraft(store: DraftStore | null, key: string): SourceDraft | null {
  try {
    const raw = store?.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<SourceDraft>;
    return typeof parsed.text === "string" && typeof parsed.savedAt === "string"
      ? { text: parsed.text, savedAt: parsed.savedAt }
      : null;
  } catch {
    return null;
  }
}

export function writeDraft(store: DraftStore | null, key: string, text: string, now = new Date()): void {
  try {
    if (!text.trim()) store?.removeItem(key);
    else store?.setItem(key, JSON.stringify({ text, savedAt: now.toISOString() }));
  } catch {
    // 隐私模式 / 配额满：草稿保险失效，但不打断编辑
  }
}

export function clearDraft(store: DraftStore | null, key: string): void {
  try {
    store?.removeItem(key);
  } catch {
    // 同上
  }
}

/**
 * 打开页面时要不要用本地草稿。只有它和服务端那份不一样才有意义——
 * 一样就说明上次已经保存成功，留着只会每次都弹一句"已恢复草稿"。
 */
export function draftToRestore(draft: SourceDraft | null, saved: string): SourceDraft | null {
  return draft && sourceDirty(draft.text, saved) ? draft : null;
}

// ---------------------------------------------------------------- 保存结果并回本地

/**
 * 把 `PUT /source` 的返回值并进本地的编排状态快照。
 *
 * 保存成功后**不能只靠重拉**：重拉失败（网络抖一下、某个接口 5xx）时界面会停在
 * 旧原文、仍显示"有未保存的修改"，用户以为没存上又存一遍，或者干脆放弃修改，
 * 而后端其实已经是新原文。PUT 的返回值就是后端写库后的原文与阶段，直接用它。
 *
 * 与后端 `replace_source` 同一条规则：原文真变了且退回了 routing，Router 的旧路线
 * 作废（后端删了 `router`）。快照还没取到时造一份最小快照——能走到保存这一步，
 * 后端已确认这个项目没有任何阶段产出，所以没有别的字段可丢。
 */
export function applySavedSource(
  snapshot: ProjectStateSnapshot | null,
  saved: SourceSaved,
  projectId: string,
  now = new Date(),
): ProjectStateSnapshot {
  const state: Record<string, unknown> = { ...(snapshot?.current_state_json ?? {}), source: saved.source };
  if (saved.changed && saved.stage === "routing") {
    delete state.router;
    state.stage = "routing";
  }
  return {
    project_id: snapshot?.project_id ?? projectId,
    stale_roles: snapshot?.stale_roles ?? [],
    updated_at: saved.changed ? now.toISOString() : (snapshot?.updated_at ?? now.toISOString()),
    stage: saved.stage,
    current_state_json: state,
  };
}
