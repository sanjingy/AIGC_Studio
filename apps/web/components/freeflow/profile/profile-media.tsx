"use client";

import { Loader2, RefreshCw, Sparkles } from "lucide-react";

import { PromptPanel } from "@/components/freeflow/project/prompt-panel";
import { RenderFailure, canRetryRender } from "@/components/freeflow/render-failure";
import { OutdatedNotice } from "@/components/freeflow/storyboard/shot-detail";
import { ShotStage } from "@/components/freeflow/storyboard/shot-stage";
import { ImageSourcePicker } from "@/components/project/image-source-picker";
import { BaseImageActions } from "@/components/project/render-slot";
import { Button } from "@/components/ui/button";
import { usePreparedPrompt } from "@/lib/freeflow/prepared-prompts";
import type { Images } from "@/lib/freeflow/use-images";
import { useLocalRuntime } from "@/lib/freeflow/use-local-runtime";

/**
 * 角色立绘 / 场景四视图的大图区。
 *
 * 全部由现成件拼成，不另写协议：`ShotStage`（大图 + 出图记录）、
 * `ImageSourcePicker`（平台 / 本机）、`PromptPanel`（查看 / 准备提示词）、
 * `BaseImageActions`（资产库选图 / 本地上传），生成与重试走 `useImages`。
 *
 * `blockedReason` 不为空时（档案有未保存改动或正在写库）四类动作全部禁用：
 * 此刻出图用的是库里的旧档案，不是用户眼前的草稿。
 *
 * 父组件按对象为 key 重建它：看历史的临时选择、提示词面板的内部状态都跟着
 * 清掉；出图错误本来就按对象存（`errorOf`），不会串到下一个对象。
 */
export function ProfileMedia({
  subject,
  name,
  renders,
  urlOf,
  editedAt,
  blockedReason,
}: {
  subject: { kind: "character" | "scene"; ref: string };
  name: string;
  renders: Images;
  urlOf: (assetId: string | null | undefined) => string | undefined;
  /** 这个对象最后一次字段改动的时间；图比它旧就提示可能过期 */
  editedAt: number | null;
  blockedReason: string | null;
}) {
  const runtime = useLocalRuntime(renders.projectId);
  const isScene = subject.kind === "scene";
  const history = renders.historyOf(subject);
  const latest = history[0] ?? null;
  const current = history.find((h) => h.assetId) ?? null;
  const pending = renders.isPending(subject);
  const running = latest?.status === "queued" || latest?.status === "running";
  const busy = pending || running;
  const failed = latest?.status === "failed" || latest?.status === "cancelled";
  const error = renders.errorOf(subject);
  const prepared = usePreparedPrompt(renders.projectId, subject.kind, subject.ref);
  const blocked = Boolean(blockedReason);
  const outdated = current !== null && editedAt !== null && Date.parse(current.createdAt) < editedAt;
  const noun = isScene ? "四视图参考图" : "基准立绘";

  return (
    <ShotStage
      code={name}
      title={noun}
      current={current}
      history={history}
      urlOf={urlOf}
      frameClassName={isScene ? "aspect-square max-h-[min(70vh,640px)]" : "aspect-[3/4] max-h-[min(70vh,640px)]"}
      fit="contain"
      noun={noun}
      actions={
        <>
          <ImageSourcePicker runtime={runtime} disabled={busy || blocked} className="w-36" />
          <Button
            size="sm"
            variant="primary"
            disabled={busy || blocked}
            title={
              blockedReason ??
              (runtime.source === "local"
                ? "用你电脑上的 Codex 出图：消耗你自己的订阅额度，平台 Credits 仍按同价计费"
                : current
                  ? "再出一张，会再扣一次 Credits；旧图保留在记录里"
                  : "真实出图，会扣 Credits")
            }
            onClick={() => renders.generate(subject, runtime.source)}
          >
            {busy ? <Loader2 aria-hidden className="size-3.5 animate-spin" /> : <Sparkles aria-hidden className="size-3.5" />}
            {pending ? "提交中" : running ? "生成中" : current ? "再出一张" : `生成${noun}`}
          </Button>
          {failed && latest?.taskId && canRetryRender(latest.errorCode) && (
            <Button size="sm" disabled={busy || blocked} title={blockedReason ?? undefined} onClick={() => renders.retry(subject, latest.taskId as string)}>
              <RefreshCw aria-hidden className="size-3.5" />
              重试这次任务
            </Button>
          )}
          {renders.projectId && (
            <PromptPanel
              projectId={renders.projectId}
              kind={subject.kind}
              subjectKey={subject.ref}
              disabled={busy || blocked}
              disabledReason={blockedReason ?? (running ? "这一张正在生成，完成后再准备提示词" : undefined)}
            />
          )}
          <BaseImageActions
            subject={subject}
            renders={renders}
            pickerTitle={`选一张图作为「${name}」的${noun}`}
            disabled={busy || blocked}
            disabledReason={blockedReason}
          />
        </>
      }
      status={
        <>
          {blockedReason && <p className="text-xs text-rf-warning">{blockedReason}</p>}
          {prepared && !blocked && <p className="text-xs text-fg-subtle">出图将使用你已准备的提示词</p>}
          {outdated && editedAt !== null && <OutdatedNotice editedAt={editedAt} changed="档案改动" content="当前档案" />}
          <RenderFailure
            error={error}
            errorCode={renders.errorCodeOf(subject)}
            lastFailedCode={failed ? (latest?.errorCode ?? null) : null}
          />
        </>
      }
    />
  );
}
