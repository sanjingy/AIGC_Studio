import { ProjectLobby } from "@/components/freeflow/project-lobby";

/**
 * 01 首页（需求文档「屏幕 01」、REQ-010/011）。
 *
 * 三段从上往下：最近项目 → 快速开始 → 推荐模板。前两段接真实接口
 * （`projects.list()` / `projects.create()`），第三段是示例数据，
 * 因为后端没有模板表——各自在组件里写清楚了哪一段是真的。
 *
 * 页面本身不取数，三段各自是 client 组件、各自取数：一段挂了另外两段
 * 照常显示，也不用为了一个 useEffect 把整页变成 client。
 */
export default function FreeflowHomePage() {
  return (
    <ProjectLobby />
  );
}
