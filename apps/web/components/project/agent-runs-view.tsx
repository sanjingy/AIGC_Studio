import type { AgentRun } from "@/lib/api";

/** Agent 运行记录。诊断信息，默认折叠着——它不是用户要看的主要内容。 */
export function AgentRunsView({ runs }: { runs: AgentRun[] }) {
  if (runs.length === 0) {
    return <p className="px-3 py-6 text-center text-sm text-fg-subtle">还没有运行记录</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-border text-left text-fg-subtle">
            <th className="px-3 py-1.5 font-medium">角色</th>
            <th className="px-3 py-1.5 font-medium">状态</th>
            <th className="px-3 py-1.5 font-medium">模型</th>
            <th className="px-3 py-1.5 text-right font-medium">token</th>
            <th className="px-3 py-1.5 font-medium">错误</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id} className="border-b border-border last:border-0">
              <td className="px-3 py-1.5">{r.role}</td>
              <td className="px-3 py-1.5">
                <span
                  className={
                    r.status === "succeeded"
                      ? "text-success"
                      : r.status === "failed"
                        ? "text-danger"
                        : "text-running"
                  }
                >
                  {r.status}
                </span>
              </td>
              <td className="px-3 py-1.5 text-fg-muted">{r.model_id ?? "—"}</td>
              <td className="tnum px-3 py-1.5 text-right text-fg-muted">
                {r.tokens_in}→{r.tokens_out}
              </td>
              <td className="px-3 py-1.5 text-danger">{r.error_code ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
