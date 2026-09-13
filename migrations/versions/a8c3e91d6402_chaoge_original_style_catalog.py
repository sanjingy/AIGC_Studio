"""Add the eight original Chaoge v1 styles without changing locked projects.

ADR-038. Tokens transcribed verbatim from the user-provided 画风词库.md.
No runtime dependency on the source skill directory. Old catalog rows, default
style selection and project snapshots are preserved. Downgrade deactivates only
these new choices so any existing lock can still resolve its source key.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import insert

revision: str = "a8c3e91d6402"
down_revision: str | None = "f1c8d05e37a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE_TOKENS_SHA256 = "6812384de2647cb40fcb29732e9025e3c7b0d64622240989c273d44fff74d980"

STYLES = [
    {
        "key": "chaoge_v1_01",
        "name": "写实影视风",
        "description": "超哥原版①，人物、场景、视频分别使用原版完整描述词。",
        "base_model": "wan2.2-t2i-flash",
        "character_tokens": "写实影视风格，电影级布光，戏剧性光影，好莱坞大片质感，Cinema级渲染，真人演员质感",
        "scene_tokens": "写实影视风格，电影级布光，戏剧性光影，好莱坞大片质感，高质量实景空场景",
        "video_tokens": "电影写实风，ARRI摄影机质感，24fps电影帧率，自然景深，戏剧性三点布光，轻微手持晃动感，电影级调色",
        "negative_tokens": "",
        "color_grading": "",
        "line_weight": "",
        "render_mode": "",
        "sort_order": 1,
        "is_active": True,
    },
    {
        "key": "chaoge_v1_02",
        "name": "AI短剧风",
        "description": "超哥原版②，人物、场景、视频分别使用原版完整描述词。",
        "base_model": "wan2.2-t2i-flash",
        "character_tokens": "原生相机拍摄质感，自然环境光，无风格化调色，色彩真实还原，自然皮肤质感，日常生活光线，画面清透干净",
        "scene_tokens": "原生相机拍摄质感，自然环境光，无风格化调色，色彩真实还原，高质量实景空场景，日常自然光线",
        "video_tokens": "竖屏短剧风格，现代真实感，自然环境光，无风格化调色，画面清透干净，色彩还原真实，1080P高清，画面流畅稳定，人物细节清晰，轻微手持感，生活化镜头语言",
        "negative_tokens": "",
        "color_grading": "",
        "line_weight": "",
        "render_mode": "",
        "sort_order": 2,
        "is_active": True,
    },
    {
        "key": "chaoge_v1_03",
        "name": "超写实摄影",
        "description": "超哥原版③，人物、场景、视频分别使用原版完整描述词。",
        "base_model": "wan2.2-t2i-flash",
        "character_tokens": "超写实摄影风格，8K超高清，电影级布光，真人质感，细腻皮肤纹理，毛孔级细节，专业摄影棚灯光，Octane渲染，RAW格式质感",
        "scene_tokens": "超写实摄影风格，8K超高清，Octane渲染，极致真实细节，高精度实景空场景，RAW格式质感",
        "video_tokens": "超写实纪录片风格，高速摄影质感，自然光源，极致细节还原，微距镜头感，真实环境噪点，RAW格式色彩空间",
        "negative_tokens": "",
        "color_grading": "",
        "line_weight": "",
        "render_mode": "",
        "sort_order": 3,
        "is_active": True,
    },
    {
        "key": "chaoge_v1_04",
        "name": "3D国漫风",
        "description": "超哥原版④，人物、场景、视频分别使用原版完整描述词。",
        "base_model": "wan2.2-t2i-flash",
        "character_tokens": "3D国漫风格，虹彩工场，UE5引擎渲染，高精度3D建模，PBR材质，全局光照，细腻的服装布料模拟，电影级灯光",
        "scene_tokens": "3D国漫风格，虹彩工场，UE5引擎渲染，高质量空场景，高精度3D场景建模，PBR材质，全局光照，电影级灯光",
        "video_tokens": "3D国漫动画风格，虹彩工场/原神质感，流畅补间动画，24fps，PBR材质光影，服装布料动态模拟，电影级灯光动效",
        "negative_tokens": "",
        "color_grading": "",
        "line_weight": "",
        "render_mode": "",
        "sort_order": 4,
        "is_active": True,
    },
    {
        "key": "chaoge_v1_05",
        "name": "3D欧美风",
        "description": "超哥原版⑤，人物、场景、视频分别使用原版完整描述词。",
        "base_model": "wan2.2-t2i-flash",
        "character_tokens": "3D皮克斯动画风格，迪士尼质感，高饱和度色彩，圆润造型，柔和灯光，Cinema 4D渲染质感",
        "scene_tokens": "3D皮克斯动画风格，迪士尼场景质感，Cinema 4D渲染，高质量空场景，高饱和度色彩，柔和灯光",
        "video_tokens": "3D皮克斯动画风格，迪士尼运动曲线，夸张流畅的缓入缓出，高饱和度色彩，柔和动态灯光，24fps",
        "negative_tokens": "",
        "color_grading": "",
        "line_weight": "",
        "render_mode": "",
        "sort_order": 5,
        "is_active": True,
    },
    {
        "key": "chaoge_v1_06",
        "name": "日系动漫",
        "description": "超哥原版⑥，人物、场景、视频分别使用原版完整描述词。",
        "base_model": "wan2.2-t2i-flash",
        "character_tokens": "日系动漫插画风格，京都动画质感，厚涂手绘笔触，赛璐珞上色，精致线稿，鲜艳色彩",
        "scene_tokens": "日系动漫场景插画风格，京都动画背景质感，高质量空场景背景，厚涂手绘笔触，精致线稿，鲜艳色彩",
        "video_tokens": "日系动漫风格，京都动画质感，赛璐珞动画，8/12fps有限动画帧率，手绘线稿动态，鲜艳色彩，特效粒子动画",
        "negative_tokens": "",
        "color_grading": "",
        "line_weight": "",
        "render_mode": "",
        "sort_order": 6,
        "is_active": True,
    },
    {
        "key": "chaoge_v1_07",
        "name": "国风水墨",
        "description": "超哥原版⑦，人物、场景、视频分别使用原版完整描述词。",
        "base_model": "wan2.2-t2i-flash",
        "character_tokens": "中国传统工笔画风格，水墨渲染，留白构图，淡雅设色，宣纸纹理，写意笔触",
        "scene_tokens": "中国传统山水画风格，水墨渲染，高质量空场景，留白构图，淡雅设色，宣纸纹理，写意笔触",
        "video_tokens": "中国传统水墨动画风格，写意笔触动态，墨迹晕染扩散效果，留白构图，淡雅色彩流动，宣纸纹理质感",
        "negative_tokens": "",
        "color_grading": "",
        "line_weight": "",
        "render_mode": "",
        "sort_order": 7,
        "is_active": True,
    },
    {
        "key": "chaoge_v1_08",
        "name": "欧美漫画",
        "description": "超哥原版⑧，人物、场景、视频分别使用原版完整描述词。",
        "base_model": "wan2.2-t2i-flash",
        "character_tokens": "欧美漫画风格，DC/Marvel画风，强对比度，硬朗线条，厚重阴影，英雄主义构图",
        "scene_tokens": "欧美漫画场景风格，DC/Marvel背景画风，高质量空场景，强对比度，硬朗线条，厚重阴影",
        "video_tokens": "欧美漫画动态风格，DC/Marvel动态分镜感，强对比度，速度线动效，硬朗线条，厚重阴影流动，英雄主义运镜",
        "negative_tokens": "",
        "color_grading": "",
        "line_weight": "",
        "render_mode": "",
        "sort_order": 8,
        "is_active": True,
    },
]


def _catalog() -> sa.TableClause:
    return sa.table(
        "style_catalog",
        *[
            sa.column(
                key,
                sa.Boolean
                if key == "is_active"
                else sa.Integer
                if key == "sort_order"
                else sa.Text,
            )
            for key in STYLES[0]
        ],
    )


def upgrade() -> None:
    catalog = _catalog()
    # Re-upgrade only reactivates our choices, never overwrites edited tokens.
    statement = insert(catalog).values(STYLES)
    op.execute(
        statement.on_conflict_do_update(index_elements=[catalog.c.key], set_={"is_active": True})
    )


def downgrade() -> None:
    catalog = _catalog()
    op.execute(
        catalog.update()
        .where(catalog.c.key.in_([row["key"] for row in STYLES]))
        .values(is_active=False)
    )
