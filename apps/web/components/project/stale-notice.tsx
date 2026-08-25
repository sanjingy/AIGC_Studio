"use client";

import { Loader2, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * 「上游已更新，建议同步」。
 *
 * 出现在折叠组的组头上，折起来也看得见——这正是它存在的意义：
 * 改完剧本之后，用户不会主动去展开角色组检查它是不是过期了。
 *
 * 同步按钮不走新接口，就是一次普通的 revise。上游数据由
 * `orchestrator.variables_for` 从最新 state 里现取，所以只要发一句
 * "按最新设定同步"，模型拿到的上游就已经是新版了。
 */
export function StaleNotice({ busy, onSync }: { busy: boolean; onSync: () => void }) {
  return (
    <div className="flex items-center gap-2">
      <span className="flex items-center gap-1.5 text-xs text-running">
        <span aria-hidden className="size-1.5 rounded-full bg-running" />
        上游已更新，建议同步
      </span>
      <Button size="sm" disabled={busy} onClick={onSync}>
        {busy ? (
          <Loader2 aria-hidden className="size-3.5 animate-spin" />
        ) : (
          <RefreshCw aria-hidden className="size-3.5" />
        )}
        {busy ? "同步中…" : "同步最新设定"}
      </Button>
    </div>
  );
}
