"use client";

/**
 * 出图状态与出图动作。
 *
 * 实现就是 `lib/useRenders.ts`——它已经是全站唯一那份（旧壳、freeflow 的
 * 角色/场景/分镜三处共用），本轮不抄第二份出来。这里只是把它收进
 * `lib/freeflow/` 的领域 hook 命名下，让页面统一从这一层取数。
 *
 * 三类出图落在同一个 hook 上：
 * - 角色基准立绘 `POST /projects/{id}/images/characters/{ref}`
 * - 场景参考图   `POST /projects/{id}/images/scenes/{ref}`
 * - 单镜首帧     `POST /projects/{id}/images/shots/{index}`
 *
 * 以及两条不花钱的"钉图"路径（PUT，幂等）：从资产库选一张、本地上传一张。
 * 提示词一律由后端 `consistency.compose` 合成，前端传不了也不该传。
 */
export {
  useRenders as useImages,
  subjectKey,
  type RenderSubject,
  type RenderView,
  type Renders as Images,
} from "@/lib/useRenders";
