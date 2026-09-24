/**
 * 本地上传四个阶段的报错文案。**纯函数，无路径别名**，`node --test` 直接测。
 *
 * 三段式直传（建 pending 资产 → PUT 对象存储 → complete）再加最后一步
 * "钉成角色/场景基准图"，任何一步失败用户看到的都曾是同一句"文件上传失败"，
 * 分不清是没拿到上传票据、文件没传过去、服务端没登记上，还是图传上去了只是
 * 没钉成功。这里给每一步一个错误码和一句能定位的话。
 *
 * 三条约束：
 * - 后端给了 `user_message` 就**保留它**（配额满、类型不支持、太大都在那句里），
 *   只在前面加上是哪一步。
 * - **不出现上传地址、Key、堆栈**。预签名 URL 里带着签名，贴到界面上等于
 *   让用户截图把它发出去。PUT 那一步只报 HTTP 状态码。
 * - 文案只说事实和下一步，不说"请重试"之外的猜测。
 */

export type UploadStage = "create" | "transfer" | "complete" | "bind";

export const UPLOAD_STAGE_CODES: Record<UploadStage, string> = {
  create: "asset.upload.create_failed",
  transfer: "asset.upload.put_failed",
  complete: "asset.upload.complete_failed",
  bind: "asset.upload.bind_failed",
};

const STAGE_LABEL: Record<UploadStage, string> = {
  create: "创建上传失败",
  transfer: "文件传输失败",
  complete: "完成登记失败",
  bind: "设为基准图失败",
};

export type StageErrorInput = {
  stage: UploadStage;
  /** 后端错误里的 user_message（有就保留） */
  backendMessage?: string | null;
  /** 对象存储 PUT 的 HTTP 状态码；0 = 连不上（网络 / CORS），没有响应 */
  status?: number;
};

export function uploadStageMessage({ stage, backendMessage, status }: StageErrorInput): string {
  const head = STAGE_LABEL[stage];
  if (stage === "transfer") {
    if (!status) return `${head}：连不上对象存储，文件没有传上去`;
    return `${head}：对象存储拒绝了这次上传（HTTP ${status}），请重试`;
  }
  const detail = sanitize(backendMessage);
  if (detail) return `${head}：${detail}`;
  if (stage === "create") return `${head}：没有拿到上传地址，文件还没开始传`;
  if (stage === "complete") return `${head}：文件已传到存储，但服务端没有确认，请重试`;
  return `${head}：图片已上传到资产库，可以在「从资产库选择」里重新选它`;
}

/** 后端文案一般是干净的；万一带了 URL 或像 Key 的串，整段抹掉，只留前半句。 */
function sanitize(text: string | null | undefined): string {
  const value = (text ?? "").trim();
  if (!value) return "";
  if (/https?:\/\//i.test(value) || /\bsk-[A-Za-z0-9_-]{4,}/.test(value)) {
    return (value.split(/https?:\/\/|\bsk-/i)[0] ?? "").replace(/[：:，,\s]+$/, "");
  }
  return value.length > 160 ? `${value.slice(0, 160)}…` : value;
}
