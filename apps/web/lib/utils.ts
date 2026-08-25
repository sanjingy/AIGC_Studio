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

/** 字节数 → 人看得懂的大小。资产库的用量条与文件列表用。 */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value >= 10 || i === 0 ? Math.round(value) : value.toFixed(1)} ${units[i]}`;
}
