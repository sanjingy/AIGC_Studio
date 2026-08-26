import { ModelCatalogPage } from "@/components/freeflow/model-catalog-page";

/**
 * 05 模型（全局层）。
 *
 * 不再是占位页：能力与可选模型来自 `/model-catalog`，计费账号来自
 * `/provider-credentials`，两个都是真实接口。还没接入的能力（视频、语音）
 * 照实标注为未接入，不给下拉框——理由和取舍写在组件里。
 */
export default function FreeflowModelsPage() {
  return <ModelCatalogPage />;
}
