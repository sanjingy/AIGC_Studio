import { ModelSetupLink } from "@/components/freeflow/model-setup-link";
import { needsModelSetup } from "@/lib/freeflow/provider-scope";

/**
 * 出图位上的两种失败提示，角色/场景大图区与分镜首帧共用。
 *
 * - `error` / `errorCode`：这一次请求当场失败（`useRenders.errorOf / errorCodeOf`）；
 * - `lastFailedCode`：上一条出图任务在 Worker 里失败的错误码。
 *
 * 任一个是 `provider.not_configured`（一个可用模型都没有）就附「去模型库」，
 * 且不说"可以重试"——那种失败重试多少次都一样。按码判断，不匹配文案。
 */
export function RenderFailure({
  error,
  errorCode,
  lastFailedCode,
}: {
  error: string | null;
  errorCode: string | null;
  lastFailedCode: string | null;
}) {
  return (
    <>
      {error && (
        <div role="alert" className="flex flex-wrap items-center gap-2 rounded-md bg-danger-soft px-3 py-2 text-xs break-words text-danger">
          <p className="min-w-0 flex-1">{error}</p>
          {needsModelSetup(errorCode) && <ModelSetupLink size="sm" />}
        </div>
      )}
      {lastFailedCode &&
        (needsModelSetup(lastFailedCode) ? (
          // 当场的错误已经带了链接就不再重复一条
          !needsModelSetup(errorCode) && (
            <div className="flex flex-wrap items-center gap-2 text-xs text-danger">
              <p className="min-w-0 flex-1">上次出图失败：还没有可用的图片生成模型。去模型库添加供应商并设为默认后再出图。</p>
              <ModelSetupLink size="sm" />
            </div>
          )
        ) : (
          <p className="text-xs text-danger">上次出图失败，错误码：{lastFailedCode}。可以重试这次任务，或再出一张。</p>
        ))}
    </>
  );
}

/** 「重试这次任务」该不该出现：没有可用模型时重试只会被后端拒（409），不给这个入口。 */
export function canRetryRender(lastFailedCode: string | null): boolean {
  return !needsModelSetup(lastFailedCode);
}
