import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** 把 Credits（最小单位）格式化为人民币。1 Credit = ¥0.01，见 08_BillingCredits.md */
export function formatCredits(credits: number): string {
  return credits.toLocaleString("zh-CN");
}

export function creditsToYuan(credits: number): string {
  return `¥${(credits / 100).toFixed(2)}`;
}

/** 毫秒 → mm:ss.f 时间码 */
export function timecode(ms: number): string {
  const total = ms / 1000;
  const m = Math.floor(total / 60);
  const s = Math.floor(total % 60);
  const f = Math.floor((total % 1) * 10);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${f}`;
}
