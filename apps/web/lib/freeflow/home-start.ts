/**
 * 首页「只创建项目」与「开始生产」两个动作的规则。
 *
 * 后端事实：`POST /projects` 只收标题；原文只能经
 * `POST /projects/{id}/advance` 的 `user_input` 写入，而这一步会同步调用模型
 * 跑到第一道审核门、扣 Credits。所以两个动作必须分开：
 *
 * - 只创建项目：只发 create，原文不保存。
 * - 开始生产：确认后 create → advance。advance 失败时项目已经建好，重试只再发
 *   advance，不能再建一个同名项目。
 */

/** 后端 `ProjectCreateIn.title` 的上限。 */
export const TITLE_MAX = 200;
/** 后端 `AdvanceIn.user_input` 的上限（Python 按字符计，这里按码点数）。 */
export const SOURCE_MAX = 20_000;

export type StartPhase = "idle" | "creating" | "advancing";

export type HomeDraft = {
  title: string;
  source: string;
  /** advance 失败后留下的已建项目；有值时项目名锁定，重试只发 advance。 */
  createdId: string | null;
  phase: StartPhase;
};

export type ActionState = { enabled: boolean; reason: string | null };

/** 按码点数，与后端按字符计的上限一致（emoji、生僻字不会被算成两个）。 */
export function charCount(text: string): number {
  return Array.from(text).length;
}

function titleProblem(title: string): string | null {
  const t = title.trim();
  if (!t) return "先填项目名";
  if (charCount(t) > TITLE_MAX) return `项目名不能超过 ${TITLE_MAX} 字`;
  return null;
}

function sourceProblem(source: string): string | null {
  const s = source.trim();
  if (!s) return "先粘贴小说原文或写一句创意";
  if (charCount(s) > SOURCE_MAX) return `原文超过 ${SOURCE_MAX} 字，删减后再开始`;
  return null;
}

export function startAvailability(d: HomeDraft): { createOnly: ActionState; start: ActionState } {
  if (d.phase !== "idle") {
    const reason = d.phase === "creating" ? "正在创建项目" : "正在生成，请稍候";
    return { createOnly: { enabled: false, reason }, start: { enabled: false, reason } };
  }
  const title = d.createdId ? null : titleProblem(d.title);
  const source = sourceProblem(d.source);
  return {
    // 项目已经建好时「只创建项目」没有意义——界面换成「进入项目」。
    createOnly: d.createdId
      ? { enabled: false, reason: "项目已创建" }
      : { enabled: title === null, reason: title },
    start: { enabled: title === null && source === null, reason: title ?? source },
  };
}

/** 开始生产要发哪些请求。重试时项目已存在，只发 advance。 */
export function startPlan(d: Pick<HomeDraft, "createdId">): { create: boolean; advance: true } {
  return { create: d.createdId === null, advance: true };
}

/** 原文框有内容、却要「只创建项目」时的警示。没内容就不打扰。 */
export function createOnlyWarning(source: string): string | null {
  return source.trim() ? "已填的原文不会随项目保存，进入项目后需要重新粘贴。" : null;
}
