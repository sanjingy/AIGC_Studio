"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";

/** 哨兵历史条目上的标记键。只用来判断"现在是不是已经站在哨兵上"。 */
const MARK = "__aigcDirtyGuard";

/**
 * 有未保存改动时，拦住**站内**的离开：点任何站内链接（模块栏、面包屑、品牌）、
 * 浏览器后退。
 *
 * `beforeunload`（`use-leave-guard.ts`）只管整页卸载——关标签、刷新、地址栏
 * 换地址。Next 的 `Link` 与同文档的 `popstate` 都不卸载页面，它一概看不见，
 * 草稿会被静默丢掉。这里补上那两条：
 *
 * 1. **链接**：在 `window` 的捕获阶段拦点击。这一步早于 React 挂在根节点上的
 *    监听，`stopPropagation` 之后 `Link` 自己的 onClick 根本收不到事件，
 *    `preventDefault` 再挡住原生跳转。修饰键点击（新标签打开）不拦——不会丢稿。
 * 2. **后退**：dirty 时往历史里压一条同地址的哨兵条目。用户后退只会退到它下面
 *    那条同一页的条目上（页面不动），这时立刻再压回哨兵并询问。确认离开就
 *    `history.go(-2)` 越过这两条同页条目。
 *
 * 同一路径内的跳转不算离开：
 * - 片段导航（`#plot-index` 这类旧入口）交给页面自己的 `hashchange` 处理，
 *   它会走页内的未保存确认。原生片段导航还会触发 `popstate`，这里认出来放过，
 *   否则会被当成后退、确认后 `go(-2)` 把人带出这一页。
 * - 点当前模块（同路径、只差 query）什么也不做：人已经在这一页，Next 的
 *   同路由跳转不卸载组件，放行只会把 URL 定位冲掉、草稿却还留着。
 *
 * `onBlocked(proceed)` 由调用方决定怎么问（通常是一个确认弹窗）；用户确认后
 * 调 `proceed()` 真正离开。
 *
 * 已知代价：保存之后哨兵条目仍留在历史里，下一次后退会先落在同一页的那条上，
 * 相当于"空按一次"。浏览器不给删历史条目，这是换来"后退不丢稿"的最小代价。
 */
export function useNavigationGuard(
  active: boolean,
  onBlocked: (proceed: () => void) => void,
): void {
  const router = useRouter();
  const bypass = useRef(false);
  const onBlockedRef = useRef(onBlocked);
  onBlockedRef.current = onBlocked;

  useEffect(() => {
    if (!active) return;
    bypass.current = false;

    const onClick = (e: MouseEvent) => {
      if (bypass.current || e.defaultPrevented) return;
      if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const anchor = e.target instanceof Element ? e.target.closest("a[href]") : null;
      if (!(anchor instanceof HTMLAnchorElement)) return;
      if (anchor.target && anchor.target !== "_self") return;
      if (anchor.hasAttribute("download")) return;
      const url = new URL(anchor.href, window.location.href);
      if (url.origin !== window.location.origin) return;
      if (url.href === window.location.href) return;

      e.preventDefault();
      e.stopPropagation();
      if (url.pathname === window.location.pathname) {
        // 同一页：只有片段不同才交给页面的 hashchange（它自己会问）
        if (url.hash && url.hash !== window.location.hash) window.location.hash = url.hash;
        return;
      }
      onBlockedRef.current(() => {
        bypass.current = true;
        router.push(`${url.pathname}${url.search}${url.hash}`);
      });
    };

    const pushMark = () =>
      window.history.pushState(
        { ...(window.history.state ?? {}), [MARK]: true },
        "",
        window.location.href,
      );

    if (!window.history.state?.[MARK]) pushMark();
    // 哨兵所在的地址。片段导航只改 hash，靠它和"退到哨兵下面那条"区分开
    const armed = new URL(window.location.href);

    const onPopState = () => {
      if (bypass.current) return;
      // 落在哨兵自己身上：是从片段条目退回来的，页面没离开
      if (window.history.state?.[MARK]) return;
      const here = new URL(window.location.href);
      const sameDocument = here.pathname === armed.pathname && here.search === armed.search;
      if (sameDocument && here.hash !== armed.hash) return;
      // 刚退到哨兵下面那条同页条目上：压回去，再问
      pushMark();
      onBlockedRef.current(() => {
        bypass.current = true;
        window.history.go(-2);
      });
    };

    window.addEventListener("click", onClick, true);
    window.addEventListener("popstate", onPopState);
    return () => {
      window.removeEventListener("click", onClick, true);
      window.removeEventListener("popstate", onPopState);
    };
  }, [active, router]);
}
