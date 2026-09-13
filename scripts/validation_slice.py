"""10 镜验证切片（ADR-016）。

这是 M1 里唯一要花真钱的一步，也是唯一能回答这些问题的一步：

    参考条件化的实际角色相似度能到多少？
    哪种镜头最容易崩（换装 / 侧脸 / 远景 / 多人）？
    **真实废片率是多少**——它决定 19_UnitEconomics.md 的成本结论准不准

在拿到这组数字之前，成本模型里的 image_retry_factor=2.5 只是拍的，
不能用来对外报价。

用法（在 api 容器里）：
    python scripts/validation_slice.py            # 默认 10 镜
    python scripts/validation_slice.py --shots 4  # 省钱的小样
    python scripts/validation_slice.py --dry-run  # 只打印提示词不出图
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from adapters.providers.base import ImageRequest
from apps.api.core.db import session_scope
from apps.api.core.logging import configure_logging, get_logger
from apps.api.modules.consistency import compose, metrics
from apps.api.modules.consistency import service as consistency
from apps.api.modules.gateway import service as gateway

log = get_logger(__name__)

# 刻意覆盖不同难度的镜头类型。17_ConsistencyEngine.md §9 问的是
# "哪种镜头最容易崩"，只测简单正面像回答不了这个问题。
SHOT_PLAN: list[dict[str, str]] = [
    {"kind": "正面中景", "content": "站在锈蚀的集装箱前，正对镜头，神情警觉"},
    {"kind": "侧脸近景", "content": "侧脸看向海面，雨水顺着下颌线滑落"},
    {"kind": "远景全身", "content": "远景，独自走在空旷的栈桥上，背影占画面很小一部分"},
    {"kind": "特写手部", "content": "特写，手中攥着一块停摆的怀表"},
    {"kind": "背身", "content": "背对镜头，推开一扇锈死的铁门"},
    {"kind": "低角度", "content": "低角度仰拍，站在钠灯下抬头看向天空"},
    {"kind": "动态", "content": "在雨中奔跑，风衣下摆扬起"},
    {"kind": "暗光", "content": "几乎全黑的仓库里，只有一束光打在脸上"},
    {"kind": "换装", "content": "脱掉风衣只穿高领毛衣，坐在长椅上"},
    {"kind": "多人同框", "content": "与另一名穿警服的男子在码头对峙，两人各占半边画面"},
]

CHARACTER = {
    "ref": "lin_shu",
    "name": "林舒",
    "age_range": "30 出头",
    "hair": "黑色短发，右侧偏分，发梢微卷",
    "eyes": "深褐色，眼尾下垂",
    "face": "瘦削，颧骨明显，左眉有一道旧疤",
    "build": "偏瘦，中等身高",
    "outfit": "深灰长风衣，内搭高领毛衣，黑色皮鞋",
    "distinctive": "常年戴一块停摆的银色怀表",
}


@dataclass
class ShotResult:
    index: int
    kind: str
    similarity: float | None
    passed: bool | None
    must_regenerate: bool | None
    error: str | None
    url: str | None
    prompt: str


async def run(*, shots: int, dry_run: bool) -> dict[str, Any]:
    org_id = uuid.uuid4()
    project_id = uuid.uuid4()

    async with session_scope() as db:
        style = await consistency.ensure_style(
            db, org_id=org_id, project_id=project_id, seed_base=100_000
        )
        profiles = await consistency.upsert_characters(
            db, org_id=org_id, project_id=project_id, designs=[CHARACTER]
        )
        style_id, profile = style.id, profiles[0]
        ref_prompt = compose.reference_portrait_prompt(profile, style)
        # 三套风格词各记各的（ADR-036 第 3 条）。这份切片跑的是角色立绘与
        # 镜头图，注入的是人物版；场景版与视频版一并记下来，是为了让报告能
        # 回答"当时的画风档案长什么样"，而不只是"当时用了哪一句"。
        style_snapshot = {
            "character": style.character_tokens,
            "scene": style.scene_tokens,
            "video": style.video_tokens,
            "negative": style.negative_tokens,
            "render_mode": style.render_mode,
            "seed_base": style.seed_base,
        }

    plan = SHOT_PLAN[:shots]

    if dry_run:
        print("=== 基准立绘提示词 ===")
        print(ref_prompt)
        print("\n=== 镜头提示词 ===")
        async with session_scope() as db:
            for i, spec in enumerate(plan, start=1):
                c = compose.compose_shot(
                    content=spec["content"],
                    style=style,
                    characters=[profile],
                    shot_index=i,
                )
                print(f"\n[{i}] {spec['kind']}  seed={c.seed}")
                print(f"  {c.prompt}")
        return {"dry_run": True, "shots": len(plan)}

    # ---- 1. 基准立绘 ----
    print("生成基准立绘…")
    base = await gateway.generate_image(
        ImageRequest(prompt=ref_prompt, negative_prompt=style.negative_tokens, n=1)
    )
    base_url = base.urls[0]
    reference = await metrics.embed_image_url(base_url)
    print(f"  基准立绘就绪，embedding {len(reference)} 维")
    print(f"  {base_url[:110]}…")

    # ---- 2. 逐镜生成并度量 ----
    results: list[ShotResult] = []
    for i, spec in enumerate(plan, start=1):
        composed = compose.compose_shot(
            content=spec["content"], style=style, characters=[profile], shot_index=i
        )
        print(f"[{i}/{len(plan)}] {spec['kind']} …", end=" ", flush=True)

        try:
            out = await gateway.generate_image(
                ImageRequest(
                    prompt=composed.prompt,
                    negative_prompt=composed.negative_prompt,
                    seed=composed.seed,
                    n=1,
                )
            )
            url = out.urls[0]
            emb = await metrics.embed_image_url(url)
            verdict = metrics.judge_similarity(metrics.cosine(reference, emb))

            async with session_scope() as db:
                await consistency.record_conditioning(
                    db,
                    org_id=org_id,
                    project_id=project_id,
                    shot_index=i,
                    composed=composed,
                    style_id=style_id,
                    attempt=1,
                )

            results.append(
                ShotResult(
                    index=i,
                    kind=spec["kind"],
                    similarity=round(verdict.value, 4),
                    passed=verdict.passed,
                    must_regenerate=verdict.must_regenerate,
                    error=None,
                    url=url,
                    prompt=composed.prompt,
                )
            )
            flag = "✓" if verdict.passed else ("✗重生成" if verdict.must_regenerate else "?待人工")
            print(f"{verdict.value:.4f} {flag}")
        except Exception as exc:
            results.append(
                ShotResult(
                    i, spec["kind"], None, None, None, repr(exc)[:200], None, composed.prompt
                )
            )
            print(f"失败 {type(exc).__name__}")

    return _report(results, base_url, style_snapshot)


def _report(results: list[ShotResult], base_url: str, style: dict[str, Any]) -> dict[str, Any]:
    scored = [r for r in results if r.similarity is not None]
    values = sorted(r.similarity for r in scored if r.similarity is not None)
    passed = [r for r in scored if r.passed]
    rejected = [r for r in scored if r.must_regenerate]
    errored = [r for r in results if r.error]

    # 废片率 = 平均要生成几次才得到一张可用的。
    # 这个数字直接决定 19_UnitEconomics.md 的成本结论准不准。
    usable = len(passed)
    retry_factor = round(len(results) / usable, 2) if usable else None

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "base_portrait": base_url,
        "style": style,
        "thresholds": {
            "pass": metrics.CHARACTER_SIMILARITY_PASS,
            "reject": metrics.CHARACTER_SIMILARITY_REJECT,
        },
        "counts": {
            "total": len(results),
            "passed": len(passed),
            "needs_review": len(scored) - len(passed) - len(rejected),
            "must_regenerate": len(rejected),
            "errors": len(errored),
        },
        "similarity": {
            "min": values[0] if values else None,
            "median": values[len(values) // 2] if values else None,
            "max": values[-1] if values else None,
        },
        "measured_image_retry_factor": retry_factor,
        "by_kind": sorted(
            ({"kind": r.kind, "similarity": r.similarity, "passed": r.passed} for r in scored),
            key=lambda x: (x["similarity"] is None, x["similarity"]),
        ),
        "shots": [asdict(r) for r in results],
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=int, default=10)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", default="/app/validation_report.json")
    args = parser.parse_args()

    configure_logging(level="WARNING", json_output=False)
    report = await run(shots=max(1, min(args.shots, len(SHOT_PLAN))), dry_run=args.dry_run)

    if args.dry_run:
        return

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    c = report["counts"]
    s = report["similarity"]
    print("\n" + "=" * 58)
    print(
        f"总计 {c['total']}  通过 {c['passed']}  待人工 {c['needs_review']}  "
        f"须重生成 {c['must_regenerate']}  失败 {c['errors']}"
    )
    print(f"相似度  min={s['min']}  median={s['median']}  max={s['max']}")
    print(f"实测废片率 image_retry_factor = {report['measured_image_retry_factor']}")
    print("\n分镜头类型：")
    for row in report["by_kind"]:
        mark = "✓" if row["passed"] else "✗"
        print(f"  {mark} {row['similarity']:.4f}  {row['kind']}")
    print(f"\n完整报告：{args.out}")


if __name__ == "__main__":
    asyncio.run(main())
