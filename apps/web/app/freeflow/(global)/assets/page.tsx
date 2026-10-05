import { AssetBrowser } from "@/components/freeflow/assets/asset-browser";

/**
 * 03 资产库（全局范围）。可在「全部项目 / 某个项目」之间切换；
 * 筛选、搜索、预览与上传的规则见 `components/freeflow/assets/asset-browser.tsx`。
 */
export default function FreeflowAssetsPage() {
  return <AssetBrowser />;
}
