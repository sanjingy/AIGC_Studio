/**
 * 首页「只创建项目」与「开始生产」两个动作的规则。
 *
 * 后端事实：`POST /projects` 只收标题；原文有两条写入路：
 * `PUT /projects/{id}/source` 只保存、不花钱；`POST /projects/{id}/advance` 先存原文
 * 再同步调用模型跑到第一道审核门、扣 Credits。所以两个动作分开：
 *
 * - 只创建项目：create，原文非空时再 PUT 原文（免费）。PUT 失败时项目已经建好，
 *   重试只再发 PUT，不能再建一个同名项目；原文留在页面上。
 * - 开始生产：确认后 create → advance。advance 失败时项目已经建好，重试先 PUT
 *   原文（用户可能改过）再 advance，不能再建一个同名项目。
 */

/** 后端 `ProjectCreateIn.title` 的上限。 */
export const TITLE_MAX = 200;
/** 后端 `AdvanceIn.user_input` / `SourceIn.text` 的上限（Python 按字符计，这里按码点数）。 */
export const SOURCE_MAX = 20_000;

export type StartPhase = "idle" | "creating" | "saving" | "advancing";

export type HomeDraft = {
  title: string;
  source: string;
  /** advance 或保存原文失败后留下的已建项目；有值时项目名锁定，重试不再建项目。 */
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
    const reason =
      d.phase === "creating" ? "正在创建项目" : d.phase === "saving" ? "正在保存原文" : "正在生成，请稍候";
    return { createOnly: { enabled: false, reason }, start: { enabled: false, reason } };
  }
  const title = d.createdId ? null : titleProblem(d.title);
  const source = sourceProblem(d.source);
  // 只创建项目时原文可以不填；填了就会一起保存，超长同样要先删减
  const optionalSource = d.source.trim() ? source : null;
  return {
    // 项目已经建好时「只创建项目」没有意义——界面换成「进入项目 / 重试保存原文」。
    createOnly: d.createdId
      ? { enabled: false, reason: "项目已创建" }
      : { enabled: title === null && optionalSource === null, reason: title ?? optionalSource },
    start: { enabled: title === null && source === null, reason: title ?? source },
  };
}

/**
 * 开始生产要发哪些请求。重试时项目已存在，不再建项目。
 *
 * 重试前先 PUT 一次原文：第一次 advance 已经把原文存进项目（Router 失败也存了），
 * 用户在这一页改过原文再重试时，advance 不会改写已保存的原文（409
 * `agent.source.conflict`）——换原文只能走 PUT。同一份原文 PUT 是幂等的、免费。
 */
export function startPlan(d: Pick<HomeDraft, "createdId">): { create: boolean; saveSource: boolean; advance: true } {
  return { create: d.createdId === null, saveSource: d.createdId !== null, advance: true };
}

/** 只创建项目要发哪些请求。原文为空就不 PUT；重试时项目已存在，只发 PUT。 */
export function createOnlyPlan(d: Pick<HomeDraft, "createdId" | "source">): { create: boolean; saveSource: boolean } {
  return { create: d.createdId === null, saveSource: d.source.trim() !== "" };
}

/** 原文框有内容时「只创建项目」会做什么。没内容就不打扰。 */
export function createOnlyNote(source: string): string | null {
  return source.trim() ? "原文会一起保存进项目，不调用模型、不扣 Credits；之后在故事页查看或替换。" : null;
}
