"use client";

import { useEffect } from "react";

/**
 * 有未保存改动时拦住整页离开（关标签、刷新、后退）。
 *
 * 只能拦浏览器级别的离开。App Router 没有稳定的路由拦截钩子，站内点模块栏
 * 跳走拦不住——所以除了这个之外，界面上还必须有一条一眼能看见的未保存提示，
 * 以及页内切换对象时的确认弹窗。
 *
 * 分镜页（`shot-editor.tsx`）里有一份同样的私有实现，比这里早。本轮不动它：
 * 换成共享实现没有行为收益，却要碰分镜的文件（P2A 任务书：不改变分镜行为）。
 * 下一轮如果要动 `shot-editor`，顺手换过来。
 */
export function useLeaveGuard(dirty: boolean): void {
  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      // 现代浏览器只看"有没有阻止默认行为"，文案是浏览器自己的；
      // 但 returnValue 还得赋值，否则 Safari 不弹。
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);
}
