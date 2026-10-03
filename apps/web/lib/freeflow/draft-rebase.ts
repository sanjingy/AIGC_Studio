/**
 * 编辑器草稿在库值变化后怎么跟（角色 / 场景档案、剧本字段、场编辑共用）。
 *
 * 库里的值变了（保存成功、撤销、返工、别处写库）之后：用户没动过的字段
 * （草稿仍等于**旧**库值）换成新库值；动过的保留草稿。`takeAll` 为真
 * （正是自己刚保存的结果，后端可能规范化了值）时整份换成新库值。
 *
 * 不能拿草稿去和**新**库值比来判断"有没有改动"：撤销把库值换掉的那一刻，
 * 没动过的草稿和新值也对不上，会被误当成用户的改动留下来——表单停在
 * 撤销前的值，还计成未保存，一点保存就把刚撤销的改动写回去。
 *
 * 只比一层：每个键是一个字段，值按内容比较（草稿都是纯 JSON）。
 */
export function rebaseDraft<T extends object>(prevBase: T, nextBase: T, draft: T, takeAll: boolean): T {
  if (takeAll) return nextBase;
  const prev = prevBase as Record<string, unknown>;
  const mine = draft as Record<string, unknown>;
  const out: Record<string, unknown> = {};
  for (const [key, next] of Object.entries(nextBase)) {
    out[key] = !(key in mine) || !(key in prev) || sameJson(mine[key], prev[key]) ? next : mine[key];
  }
  return out as T;
}

function sameJson(a: unknown, b: unknown): boolean {
  return a === b || JSON.stringify(a) === JSON.stringify(b);
}
