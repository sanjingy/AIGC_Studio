"use client";

import { useCallback, useEffect, useState } from "react";

import { localRuntime, type ImageSource, type LocalRuntimeStatus } from "@/lib/api";

/**
 * 本机运行时（试点）的状态与「这次出图用哪条来源」。
 *
 * 三件事在这里收口，因为它们必须一致：
 *
 * 1. **状态是全站一份**。一个项目页上有十几个出图位（每个角色、每一镜各一个），
 *    每个都自己去拉一次 `/local-runtime/status` 就是十几个请求，而它们的答案
 *    完全相同。所以缓存和轮询放在模块级，组件只订阅。
 * 2. **选择也是全站一份**。用户在角色卡上选了"本机"，切到分镜再选一次，
 *    这是同一个决定被问了两遍。选择存在模块级 + localStorage，
 *    所有出图位同步。
 * 3. **不可用时必须回落到 `api`，而且要说出原因**。选择停在一个用不了的值上，
 *    用户点生成只会看到失败；而把按钮变灰却不说为什么，他会以为产品坏了。
 *
 * 轮询间隔比服务端心跳有效期（20 秒）短：连接器一停，界面在十几秒内
 * 就该自己变成"未连接"，不需要用户刷新页面才发现。
 */

const REFRESH_MS = 12_000;
const STORAGE_KEY = "aigc.image-source";

type Snapshot = {
  status: LocalRuntimeStatus | null;
  /** 还没拿到第一次结果。用来避免"先闪一下不可用再变成可用"。 */
  loading: boolean;
};

let snapshot: Snapshot = { status: null, loading: true };
let inFlight: Promise<void> | null = null;
let lastFetched = 0;
const listeners = new Set<() => void>();

/** 用户选的来源。默认永远是 `api`——花平台的钱那条是主路径，本机是试点。 */
let chosen: ImageSource = "api";
let restored = false;

function emit(): void {
  for (const listener of listeners) listener();
}

function restoreChoice(): void {
  if (restored || typeof window === "undefined") return;
  restored = true;
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "local" || saved === "api") chosen = saved;
  } catch {
    // 隐私模式下 localStorage 会抛。记不住不是错误，默认值照样能用。
  }
}

async function refresh(force = false): Promise<void> {
  if (!force && Date.now() - lastFetched < REFRESH_MS / 2) return;
  if (inFlight) return inFlight;
  inFlight = (async () => {
    try {
      const status = await localRuntime.status();
      snapshot = { status, loading: false };
    } catch {
      // 拿不到状态就当"没有本机"。这条路径是可选来源，它挂了不该让
      // 出图界面报错——用户照样能用平台 API 那条。
      snapshot = { status: null, loading: false };
    } finally {
      lastFetched = Date.now();
      inFlight = null;
      emit();
    }
  })();
  return inFlight;
}

export type LocalRuntimeView = {
  /** 这个项目**有没有**本机来源这个选项（没开、或不在白名单里就没有）。 */
  configured: boolean;
  /** 现在能不能真的用它出图。 */
  available: boolean;
  /** 不可用的原因，直接显示给用户。可用时为 null。 */
  reason: string | null;
  /** 连接器自报的版本，排障用。 */
  version: string | null;
  provider: string | null;
  loading: boolean;
  /** 这次出图用哪条来源。不可用时恒为 `api`。 */
  source: ImageSource;
  setSource: (next: ImageSource) => void;
  refresh: () => void;
};

export function useLocalRuntime(projectId: string | null): LocalRuntimeView {
  const [, bump] = useState(0);

  useEffect(() => {
    restoreChoice();
    const listener = () => bump((n) => n + 1);
    listeners.add(listener);
    void refresh();
    const timer = window.setInterval(() => void refresh(true), REFRESH_MS);
    return () => {
      listeners.delete(listener);
      window.clearInterval(timer);
    };
  }, []);

  const status = snapshot.status;
  const inScope = Boolean(
    projectId && status?.enabled && status.project_ids.includes(projectId),
  );
  const available = Boolean(inScope && status?.image_available);

  const setSource = useCallback((next: ImageSource) => {
    chosen = next;
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // 同上：记不住就算了。
    }
    emit();
  }, []);

  return {
    configured: inScope,
    available,
    // 后端不可用时一定给了中文原因；这里只在完全拿不到状态时兜一句。
    reason: available ? null : (status?.image_unavailable_reason ?? "本机生成当前不可用"),
    version: status?.image_runner_version ?? null,
    provider: status?.image_provider ?? null,
    loading: snapshot.loading,
    // **不可用就一定回落到 api**：让选择停在一个用不了的值上，
    // 用户点生成只会拿到一个本可以提前避免的失败。
    source: available ? chosen : "api",
    setSource,
    refresh: () => void refresh(true),
  };
}
