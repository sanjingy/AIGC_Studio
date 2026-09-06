"""four gates, style catalog, project lock variables

ADR-037（四道门）+ ADR-036 第 3 条（风格词拆三套）的数据侧。

三件事：

1. **`style_catalog`**：可选画风目录，全局表（同 `model_pricing`，不带 org_id）。
   在这之前画风是 `consistency/service.py` 里一个写死的 `STYLE_PRESETS` dict，
   加一种画风就要改代码发版——和"价格写成常量"是同一类问题。

2. **`style_profiles` 拆列**：`positive_tokens` 一列变成 `character_tokens` /
   `scene_tokens` / `video_tokens` 三列（ADR-036 第 3 条，三者不得混用）。
   存量行把原来那一列**同时**拷进三列——那正是它今天的行为（一套词全场通用），
   所以拷贝不改变任何已冻结项目的产出。降级时从 `character_tokens` 拷回去。

3. **`project_lock_variables`**：门① 一次锁定的画风 / 时代背景与人种 / 改编模式。

—— 存量项目怎么处理（ADR-037 第 6 条）——

`Stage` 存在 `projects.current_state_json` 里（ADR-008），所以改阶段图就是一次
数据迁移。这一次**没有任何阶段名作废**：新增的 `await_plan` / `await_anchors`
是插在既有阶段之间的两个新值，存量项目的 stage 一个都没变、`_NEXT[stage]`
全部仍然命中，因此不需要 `_LEGACY_STAGES` 那种翻译（2026-08-18 由 2 步改 5 步
时踩过的那个坑，这次踩不到）。

要处理的是**数据**：已经越过门① 位置的项目从来没被问过画风与人种。
ADR-037 明写这类项目**不得被退回去重新确认**，所以这里给它们补一行锁定变量，
`origin='migrated'`、`confirmed_at` 留空。判据是 stage 已经走过 `plot_index`：

    routing / plot_index / story（旧名）  → 还没到门①，不补行，
                                            门① 打开时自然会建
    其余一切（含 visual 旧名、done）      → 已越过门①，补 migrated 行

画风补的是目录里的缺省项；时代背景**故意留空**——留空会在界面上显示成
"未判定"，而填一个"现代中国"会显示成一个看起来已经想好了的答案，
那正是 ADR-037 第 2 条禁止的"默认套用本国"。运行时的 `_era_of` 对空值
会退回按题材关键词推，行为与迁移前一致。

Revision ID: e4b7c9d21f38
Revises: b5e21a7c9f40
Create Date: 2026-09-05 22:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4b7c9d21f38"
down_revision: str | None = "b5e21a7c9f40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 画风目录的种子数据。
#
# 前两条（anime_suspense / ink_wash）的 key 与原 `STYLE_PRESETS` 一致，
# 存量项目的 `style_key` 回填指得上；它们的 `character_tokens` 也与原来的
# `positive_tokens` 一字不差，所以已经跑过的项目画风不变。
#
# 三套描述词的分工（ADR-036 第 3 条，不得混用）：
#   character  人物质感——皮肤、五官、服装材质
#   scene      **空**场景——明写"无人物"，场景基准图里出现人就当不了基准
#   video      帧率与运动质感——只给视频提示词用（M2），画静态图时是噪声
#
# (key, name, description, base_model,
#  character_tokens, scene_tokens, video_tokens, negative_tokens,
#  color_grading, line_weight, render_mode, sort_order)
_STYLES = [
    (
        "anime_suspense",
        "日式悬疑动画",
        "赛璐璐上色、清晰线稿的日式动画风，冷调低饱和，适合悬疑与推理题材。",
        "wan2.2-t2i-flash",
        "日式动画风格，赛璐璐上色，清晰线稿",
        "日式动画背景美术，赛璐璐上色，清晰线稿，空场景无人物",
        "日式动画风格，24fps 电影帧率，轻微手持晃动感，动作连贯自然",
        "真人照片，3D渲染，模糊，多余手指，畸变，水印，文字",
        "低饱和，冷调，高对比",
        "中等线宽",
        "赛璐璐",
        10,
    ),
    (
        "ink_wash",
        "水墨",
        "留白构图、淡彩晕染的水墨风，适合古装、仙侠与历史题材。",
        "wan2.2-t2i-flash",
        "水墨风格，留白构图，淡彩，衣袂笔触写意",
        "水墨风格背景，留白构图，淡彩远山，空场景无人物",
        "水墨风格，24fps 电影帧率，笔墨晕染随镜头缓慢流动",
        "真人照片，霓虹色，过曝，水印，文字",
        "低饱和，暖灰调",
        "细线",
        "水墨",
        20,
    ),
    (
        "cinematic_real",
        "电影实拍质感",
        "接近真人实拍的写实质感，皮肤与布料细节丰富，适合现代都市与悬疑。",
        "wan2.2-t2i-flash",
        "真人演员质感，皮肤纹理真实，布料垂坠自然，电影级人像布光",
        "高质量实景摄影，景深自然，建筑与陈设材质真实，空场景无人物",
        "真人实拍质感，24fps 电影帧率，轻微手持晃动感",
        "插画，卡通，3D渲染，塑料感，多余手指，畸变，水印，文字",
        "中低饱和，冷暖对比",
        "无线稿",
        "写实摄影",
        30,
    ),
    (
        "korean_webtoon",
        "韩式条漫厚涂",
        "厚涂上色、光影柔和的韩式条漫风，人物精致，适合都市情感与逆袭题材。",
        "wan2.2-t2i-flash",
        "韩式条漫厚涂，人物精致，光影柔和，皮肤通透",
        "韩式条漫背景厚涂，光影柔和，环境层次分明，空场景无人物",
        "韩式条漫厚涂，24fps 电影帧率，镜头运动平缓",
        "线稿感，粗糙笔触，多余手指，畸变，水印，文字",
        "中饱和，暖调",
        "弱线稿",
        "厚涂",
        40,
    ),
    (
        "american_comic",
        "美式漫画",
        "粗线稿、强阴影的美式漫画风，适合动作与英雄题材。",
        "wan2.2-t2i-flash",
        "美式漫画风格，粗线稿，硬阴影，肌肉结构清晰",
        "美式漫画背景，粗线稿，强透视，硬阴影，空场景无人物",
        "美式漫画风格，24fps 电影帧率，动作幅度大且干脆",
        "水彩，柔光，真人照片，多余手指，畸变，水印，文字",
        "高饱和，高对比",
        "粗线",
        "网点上色",
        50,
    ),
    (
        "retro_film",
        "复古胶片",
        "颗粒感与褪色调的胶片风，适合年代戏与回忆段落。",
        "wan2.2-t2i-flash",
        "复古胶片质感，颗粒感明显，人物妆发符合年代",
        "复古胶片背景，颗粒感明显，年代陈设考究，空场景无人物",
        "复古胶片质感，24fps 电影帧率，轻微闪烁与划痕",
        "数码锐化，霓虹色，现代物件，水印，文字",
        "低饱和，偏黄褪色",
        "无线稿",
        "胶片",
        60,
    ),
    (
        "cg_render",
        "3D 渲染",
        "次表面散射与全局光照的三维渲染风，适合科幻与奇幻题材。",
        "wan2.2-t2i-flash",
        "三维渲染人物，次表面散射皮肤，材质细节丰富",
        "三维渲染场景，全局光照，材质细节丰富，空场景无人物",
        "三维渲染，24fps 电影帧率，镜头运动平滑无抖动",
        "手绘感，线稿，噪点，多余手指，畸变，水印，文字",
        "中饱和，冷调",
        "无线稿",
        "三维渲染",
        70,
    ),
    (
        "guofeng_illustration",
        "国风插画",
        "工笔线条与矿物色的国风插画，适合古装、仙侠与神话题材。",
        "wan2.2-t2i-flash",
        "国风插画，工笔线条，矿物色敷彩，服饰纹样细致",
        "国风插画背景，工笔线条，矿物色敷彩，亭台楼阁结构准确，空场景无人物",
        "国风插画，24fps 电影帧率，衣袂与云雾随镜头缓慢流动",
        "赛博朋克，霓虹色，现代物件，多余手指，畸变，水印，文字",
        "中饱和，青绿与朱砂对比",
        "细线",
        "工笔",
        80,
    ),
]

_DEFAULT_STYLE_KEY = "anime_suspense"

# 已经越过门① 位置的存量项目。写成 SQL 里的一个 NOT IN 集合：
# 这三个值之外的一切 stage（含旧阶段名 `visual`、以及 `done`）都在门① 之后。
_BEFORE_PLAN_GATE = ("routing", "plot_index", "story")


def upgrade() -> None:
    # ---------------------------------------------------------- 画风目录
    op.create_table(
        "style_catalog",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("base_model", sa.String(length=64), nullable=False),
        sa.Column("character_tokens", sa.Text(), nullable=False),
        sa.Column("scene_tokens", sa.Text(), nullable=False),
        sa.Column("video_tokens", sa.Text(), nullable=False),
        sa.Column("negative_tokens", sa.Text(), nullable=False),
        sa.Column("color_grading", sa.String(length=120), nullable=False),
        sa.Column("line_weight", sa.String(length=40), nullable=False),
        sa.Column("render_mode", sa.String(length=40), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_style_catalog")),
        sa.UniqueConstraint("key", name=op.f("uq_style_catalog_key")),
    )
    op.create_index(
        op.f("ix_style_catalog_deleted_at"), "style_catalog", ["deleted_at"], unique=False
    )
    op.create_index(
        "ix_style_catalog_active_order", "style_catalog", ["is_active", "sort_order"], unique=False
    )

    catalog = sa.table(
        "style_catalog",
        sa.column("key", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("base_model", sa.String),
        sa.column("character_tokens", sa.Text),
        sa.column("scene_tokens", sa.Text),
        sa.column("video_tokens", sa.Text),
        sa.column("negative_tokens", sa.Text),
        sa.column("color_grading", sa.String),
        sa.column("line_weight", sa.String),
        sa.column("render_mode", sa.String),
        sa.column("sort_order", sa.Integer),
        sa.column("is_active", sa.Boolean),
    )
    op.bulk_insert(
        catalog,
        [
            {
                "key": key,
                "name": name,
                "description": description,
                "base_model": base_model,
                "character_tokens": character_tokens,
                "scene_tokens": scene_tokens,
                "video_tokens": video_tokens,
                "negative_tokens": negative_tokens,
                "color_grading": color_grading,
                "line_weight": line_weight,
                "render_mode": render_mode,
                "sort_order": sort_order,
                "is_active": True,
            }
            for (
                key,
                name,
                description,
                base_model,
                character_tokens,
                scene_tokens,
                video_tokens,
                negative_tokens,
                color_grading,
                line_weight,
                render_mode,
                sort_order,
            ) in _STYLES
        ],
    )

    # ------------------------------------------------- style_profiles 拆三套
    op.add_column(
        "style_profiles",
        sa.Column("style_key", sa.String(length=64), nullable=False, server_default=""),
    )
    for col in ("character_tokens", "scene_tokens", "video_tokens"):
        op.add_column(
            "style_profiles", sa.Column(col, sa.Text(), nullable=False, server_default="")
        )

    # 存量行：原来那一套词同时进三列。这**就是**它今天的行为——一套词全场
    # 通用——所以已冻结项目的产出一个字都不会变。
    op.execute(
        """
        UPDATE style_profiles
           SET character_tokens = positive_tokens,
               scene_tokens     = positive_tokens,
               video_tokens     = positive_tokens
        """
    )
    # 存量行认不出当初选的是哪一条目录项（那时还没有目录），按缺省画风标注。
    op.execute(
        sa.text("UPDATE style_profiles SET style_key = :key WHERE style_key = ''").bindparams(
            key=_DEFAULT_STYLE_KEY
        )
    )
    op.drop_column("style_profiles", "positive_tokens")

    # ---------------------------------------------------- 项目级锁定变量
    op.create_table(
        "project_lock_variables",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("style_key", sa.String(length=64), nullable=False),
        sa.Column("era", sa.String(length=40), nullable=False),
        sa.Column("region", sa.String(length=40), nullable=False),
        sa.Column("ethnicity", sa.String(length=60), nullable=False),
        sa.Column("era_evidence", sa.Text(), nullable=False),
        sa.Column("adaptation_mode", sa.String(length=16), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", sa.Uuid(), nullable=True),
        sa.Column("anchors_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_lock_variables")),
    )
    op.create_index(
        op.f("ix_project_lock_variables_deleted_at"),
        "project_lock_variables",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_project_lock_variables_org_id"),
        "project_lock_variables",
        ["org_id"],
        unique=False,
    )
    # 唯一索引不是优化，是约束：两行锁定变量意味着"这个项目的画风是什么"
    # 有两个答案。
    op.create_index(
        "uq_project_lock_variables_project",
        "project_lock_variables",
        ["project_id"],
        unique=True,
    )

    # 已经越过门① 的存量项目：补一行 migrated，画风取缺省，时代背景留空。
    # `WHERE NOT EXISTS` 让这条可重复执行——`alembic downgrade -1` 之后
    # 再 `upgrade head` 不会插出第二行。
    op.execute(
        sa.text(
            """
            INSERT INTO project_lock_variables (
                org_id, project_id, style_key, era, region, ethnicity, era_evidence,
                adaptation_mode, origin
            )
            SELECT p.org_id, p.id, :style_key, '', '', '', '', 'adapt', 'migrated'
              FROM projects p
             WHERE p.deleted_at IS NULL
               AND COALESCE(p.current_state_json->>'stage', 'routing') NOT IN :before_gate
               AND NOT EXISTS (
                     SELECT 1 FROM project_lock_variables l
                      WHERE l.project_id = p.id AND l.deleted_at IS NULL
                   )
            """
        ).bindparams(
            sa.bindparam("style_key", value=_DEFAULT_STYLE_KEY),
            sa.bindparam("before_gate", value=_BEFORE_PLAN_GATE, expanding=True),
        )
    )


def downgrade() -> None:
    # 每个 migration 必须可回滚且经过测试（09_Database.md 第 14 节）。
    #
    # 降级会丢东西，且必须诚实说清丢的是什么：`scene_tokens` / `video_tokens`
    # 两列没有归宿（旧 schema 只有一列），锁定变量整张表也没有。**这不是
    # 可逆的**——降级之后再升级，用户在门① 选过的画风和确认过的人种判定
    # 都回不来，只会按缺省值重新补一行 migrated。降级用于回滚一次刚出问题
    # 的部署，不是用于来回切换。
    op.drop_index("uq_project_lock_variables_project", table_name="project_lock_variables")
    op.drop_index(
        op.f("ix_project_lock_variables_org_id"), table_name="project_lock_variables"
    )
    op.drop_index(
        op.f("ix_project_lock_variables_deleted_at"), table_name="project_lock_variables"
    )
    op.drop_table("project_lock_variables")

    op.add_column(
        "style_profiles",
        sa.Column("positive_tokens", sa.Text(), nullable=False, server_default=""),
    )
    # 人物版拷回去：三套里它最接近旧语义（旧的那一套词是照着"画有人的画面"
    # 写的），拿场景版拷回去会让存量项目的角色立绘突然带上"空场景无人物"。
    op.execute("UPDATE style_profiles SET positive_tokens = character_tokens")
    op.drop_column("style_profiles", "video_tokens")
    op.drop_column("style_profiles", "scene_tokens")
    op.drop_column("style_profiles", "character_tokens")
    op.drop_column("style_profiles", "style_key")

    op.drop_index("ix_style_catalog_active_order", table_name="style_catalog")
    op.drop_index(op.f("ix_style_catalog_deleted_at"), table_name="style_catalog")
    op.drop_table("style_catalog")
