/**
 * 近期项目的排序与时间文案。
 *
 * 只认后端 `ProjectOut.updated_at`（`TimestampMixin` 的 `onupdate=now()`：改标题、
 * 写编排状态、扣费都会刷新它），文案统一叫「最近编辑」。**不拿 `created_at` 兜底**：
 * 两个字段混着排，用户看到的「最近编辑」就有一部分其实是创建时间。
 *
 * 后端列表本身按 `created_at` 分页（没有排序参数），前端只能在取到的那一页里排，
 * 这一点写在报告「需要后端」里，不在这里假装能排全量。
 */

type Dated = { id: string; created_at: string; updated_at: string };

/** 解析失败的时间排到最后，不让一条坏数据顶到第一位。 */
function stamp(value: string | null | undefined): number {
  if (!value) return Number.NEGATIVE_INFINITY;
  const t = Date.parse(value);
  return Number.isNaN(t) ? Number.NEGATIVE_INFINITY : t;
}

/** 按最近编辑倒序；同一时刻再按创建时间倒序、id，保证顺序稳定。不改入参。 */
export function sortByRecentEdit<T extends Dated>(items: readonly T[]): T[] {
  return [...items].sort((a, b) => {
    const edited = stamp(b.updated_at) - stamp(a.updated_at);
    if (edited !== 0 && !Number.isNaN(edited)) return edited;
    const created = stamp(b.created_at) - stamp(a.created_at);
    if (created !== 0 && !Number.isNaN(created)) return created;
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  });
}

const pad = (n: number) => String(n).padStart(2, "0");

/**
 * 「今天 14:05」「昨天 09:12」「09-28 14:05」「2025-09-28」。按本地时区。
 * 坏数据显示「时间未知」，不显示 Invalid Date。
 */
export function formatEditedAt(value: string, now: Date = new Date()): string {
  const t = stamp(value);
  if (!Number.isFinite(t)) return "时间未知";
  const d = new Date(t);
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const day = 24 * 60 * 60 * 1000;
  if (t >= startOfToday && t < startOfToday + day) return `今天 ${hm}`;
  if (t >= startOfToday - day && t < startOfToday) return `昨天 ${hm}`;
  if (d.getFullYear() !== now.getFullYear()) {
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${hm}`;
}
