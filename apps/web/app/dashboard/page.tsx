import { redirect } from "next/navigation";

/**
 * 旧壳工作台的地址。页面本身随 `(app)` 一起删了（ADR-030），这里只留重定向。
 *
 * 保留的理由是外部链接和浏览器书签：`/dashboard` 是登录后默认落地页用了
 * 很久的地址，直接 404 会让"以前能进的页面突然打不开"。它不进任何导航，
 * 也不是路由的一部分，只是一条兼容跳转。
 */
export default function DashboardRedirect() {
  redirect("/freeflow");
}
