import { AssetLibraryGrid } from "@/components/freeflow/asset-library-grid";

/**
 * 03 素材库（需求文档「屏幕 03」、REQ-030/031）。
 *
 * 一级类型 chip + 二级筛选 + 资产卡片网格，数据走 `assets.library()`
 * 这个已经存在的真实接口。哪几类还没有后端索引、二级筛选为什么选了
 * 不生效，都写在 `asset-library-grid.tsx` 的注释和界面文案里。
 */
export default function FreeflowAssetsPage() {
  return <AssetLibraryGrid />;
}
