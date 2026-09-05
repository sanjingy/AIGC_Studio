"""无 Key 时的出图回退（FR-CONS-011）。

文本链路早就有这一层（`agent/llm.py::get_provider`），图像链路一直没有：
`gateway.service._candidates` 发现 `image_generation` 没有任何注册路由，
直接抛 `provider.unavailable`。表现是没配 DashScope Key 时出图 100% 失败，
而 `CLAUDE.md` 承诺的是"没有 Key 也能跑通全链路"。

选择规则与 `llm.get_provider` 逐条对齐，理由也一样：

1. `ENV=test` 一律用 Mock。**这条不能靠 conftest 去设**——测试环境有 Key 时
   漏设一次就是每跑一遍测试都在真花钱（这个仓库栽过一次）。安全默认必须
   写在生产代码里。它排在 BYOK 之前：某个 org 存了自己的 DashScope Key，
   测试里跑到出图一样会打真上游。
2. 有 Key 走真实 Gateway。平台 Key 和 org 自己的 Key 都算。
3. 都没有才用 Mock。

**Mock 不进 `catalog.SPECS`**。那份目录是 `/freeflow/models` 页面渲染的
数据源，把 Mock 放进去等于让用户在下拉框里看见、甚至钉住一个假模型。
它由 `service._resolve` 在解析出口处临时兜上，路由表里不留痕迹。

产出的是**真的能 HTTP GET 到的 URL**：占位图写进对象存储，再签一个
预签名 URL 返回，和 DashScope 给一个有时效的链接完全同构。所以
`worker/jobs/generation.py` 那条"下载 → 转存 → 落库"的路径一步不少地
被走了一遍——它此前没有任何自动化测试覆盖，让它可测是这个 Mock 的
第二个价值，走捷径（直接返回字节、直接写 assets）就把这个价值丢了。
"""

from __future__ import annotations

import hashlib
import io

from PIL import Image, ImageDraw, ImageFont

from adapters.providers.base import ImageRequest, ImageResult, KeySource, signal_key_source
from apps.api.core.config import get_settings
from apps.api.core.logging import get_logger
from apps.api.modules.asset import storage
from apps.api.modules.gateway import catalog

log = get_logger(__name__)

CAPABILITY = "image_generation"
PROVIDER_ID = "provider.mock"
MODEL_ID = "mock.image.v1"

#: 占位图在对象存储里的前缀。它们不是资产（`assets` 表里那一行由
#: `register_generated` 另外写），只是给下载环节取的中转对象，
#: 单独一个前缀是为了运维能一眼认出来、也能整批清掉。
KEY_PREFIX = "mock-images"

DEFAULT_SIZE = (1024, 1024)


# ---------------------------------------------------------------- 选择规则


def forced(capability: str) -> bool:
    """规则 1：测试环境一律 Mock，有没有 Key 都一样。

    单独一个谓词而不是并进 `fallback`，是因为它必须在 BYOK 分支**之前**
    生效，而 BYOK 要查库——两件事发生的位置不同。
    """
    return capability == CAPABILITY and get_settings().env == "test"


def fallback(capability: str) -> bool:
    """规则 3：这个能力一把 Key 都没有时兜底。

    问的是"平台配了 Key 没有"，用目录里那家的 Key 去问，不写死
    DashScope——哪家提供出图是 `catalog` 说了算，在这里再抄一遍
    迟早对不上。org 自己的 Key 由调用方在这之前判掉（规则 2）。
    """
    if capability != CAPABILITY:
        return False
    spec = catalog.spec_for_capability(capability)
    return spec is None or not catalog.platform_key(spec.provider_id)


# ---------------------------------------------------------------- 占位图


def _digest(request: ImageRequest, index: int) -> str:
    """这一张图的指纹。

    把整个请求（含 seed 与第几张）都算进去：同一个提示词必须稳定出同一张图，
    不同提示词必须明显不同——否则十个镜头出十张一样的图，光看资产库
    根本判断不出流程是不是真的按镜头走了一遍。
    """
    raw = "\x00".join(
        [
            request.prompt,
            request.negative_prompt,
            request.size,
            str(request.seed),
            str(index),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_size(size: str) -> tuple[int, int]:
    """`"1024*1024"` → `(1024, 1024)`。解析不了就用默认尺寸。

    尺寸来自上层拼装的参数，出图不该因为它写错了而失败——真实上游对
    非法尺寸的反应是报参数错误，但 Mock 的职责是让链路跑通，
    在这里挑剔格式只会把无 Key 环境重新变成不可用。
    """
    for sep in ("*", "x", "X"):
        if sep in size:
            w, _, h = size.partition(sep)
            if w.strip().isdigit() and h.strip().isdigit():
                return max(64, int(w)), max(64, int(h))
    return DEFAULT_SIZE


def _color(digest: str, offset: int) -> tuple[int, int, int]:
    """从指纹里取一个颜色。

    压到中等明度区间，纯随机 RGB 会大量落在近黑近白上，
    人眼分辨不出两张图到底一不一样，也就失去了"看一眼就知道是不同镜头"
    这个唯一的用途。
    """
    chunk = digest[offset % 56 : offset % 56 + 6]
    raw = int(chunk, 16)
    return (
        60 + (raw >> 16 & 0xFF) * 140 // 255,
        60 + (raw >> 8 & 0xFF) * 140 // 255,
        60 + (raw & 0xFF) * 140 // 255,
    )


def render_placeholder(digest: str, *, size: tuple[int, int], caption: str) -> bytes:
    """画一张 PNG 占位图。纯函数，不碰存储，便于单测。

    马赛克网格 + 指纹文字。网格保证"不同提示词一眼看出不同"，
    文字保证"能把这张图对回日志里的那一次调用"——只写 ASCII：
    Pillow 自带的默认字体没有中文字形，把中文提示词画上去只会得到方框。
    """
    width, height = size
    image = Image.new("RGB", (width, height), _color(digest, 0))
    draw = ImageDraw.Draw(image)

    cells = 8
    cw, ch = width / cells, height / cells
    for row in range(cells):
        for col in range(cells):
            nibble = int(digest[(row * cells + col) % 64], 16)
            if nibble % 3 == 0:
                continue
            draw.rectangle(
                [col * cw, row * ch, (col + 1) * cw - 1, (row + 1) * ch - 1],
                fill=_color(digest, (row * cells + col) * 2 + nibble),
            )

    band_top = height // 2 - height // 10
    band_bottom = height // 2 + height // 10
    draw.rectangle([0, band_top, width, band_bottom], fill=(24, 24, 28))

    font = ImageFont.load_default(size=max(14, width // 26))
    small = ImageFont.load_default(size=max(11, width // 48))
    draw.text(
        (width // 2, band_top + (band_bottom - band_top) // 3),
        "MOCK IMAGE",
        font=font,
        fill=(240, 240, 245),
        anchor="mm",
    )
    draw.text(
        (width // 2, band_top + (band_bottom - band_top) * 2 // 3),
        caption,
        font=small,
        fill=(170, 170, 180),
        anchor="mm",
    )

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


# ---------------------------------------------------------------- Provider


class MockImageProvider:
    """假的出图 Provider。接口与 `DashScopeImageProvider` 完全一致。

    构造签名也一样（`api_key` / `model_id` / `key_source`），
    这样 Gateway 的 `Route.factory` 不需要为它开任何特例。
    """

    provider_id = PROVIDER_ID

    def __init__(
        self,
        *,
        api_key: str = "",
        model_id: str = MODEL_ID,
        key_source: KeySource = KeySource.PLATFORM,
    ) -> None:
        del api_key  # Mock 不需要凭据，签名对齐真实适配器而已
        self.model_id = model_id
        self.key_source = key_source

    async def generate_image(self, request: ImageRequest) -> ImageResult:
        signal_key_source(
            provider_id=self.provider_id, model_id=self.model_id, key_source=self.key_source
        )
        size = _parse_size(request.size)
        urls: list[str] = []
        for index in range(max(1, request.n)):
            digest = _digest(request, index)
            caption = f"{digest[:16]}  seed={request.seed}  {size[0]}x{size[1]}"
            data = render_placeholder(digest, size=size, caption=caption)
            # 键由指纹决定：同一个提示词反复出图只会覆盖同一个对象，
            # 中转文件不会随调用次数无限增长。
            key = f"{KEY_PREFIX}/{digest}.png"
            await storage.put_bytes(key=key, data=data, content_type="image/png")
            # 必须签内部地址：这个 URL 只给 Worker 自己下载用，
            # 对外地址在容器里指向进程自己。
            urls.append(await storage.presign_get(key=key, internal=True))

        log.info("gateway.mock_image", model_id=self.model_id, count=len(urls))
        # 上游没有改写提示词，如实回原文。DashScope 关掉 prompt_extend 时
        # 也是这个行为，不要在这里塞一句"这是 Mock"——那会被 `register_generated`
        # 当成上游的改写结果写进资产元数据。
        return ImageResult(
            urls=urls, model_id=self.model_id, actual_prompts=[request.prompt] * len(urls)
        )
