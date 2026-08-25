"""跑 Agent 评估最低可用版，把记分写进 tests/eval/results/（22_AgentEval.md §6）。

    python scripts/run_evals.py            # 跑分并写 tests/eval/results/agent_eval.json
    python scripts/run_evals.py --print    # 只打印，不写文件

这份 JSON 要提交进 git，用历史追踪分数变化——这就是文档「用 JSON 文件记录每次
的分数，用 git 追踪变化」的字面落地。CI 里的断言在 tests/eval/test_*.py，与这里
共用 tests.eval.eval_suite 的打分逻辑，保证两处是同一份分数。

不产生任何真实 Provider 调用：Router 用参照关键词分类器打分，schema 校验只做本地
Pydantic 解析，全程零成本。

（记分目录放在 tests/eval/results/ 而非仓库根 evals/：docker compose 没有挂载根
evals/，放进已挂载的 tests/ 树才能让容器内的 pytest 与本脚本读到同一份数据。）
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from tests.eval import eval_suite as ev

RESULTS_DIR = pathlib.Path(ev.__file__).resolve().parent / "results"
RESULTS_PATH = RESULTS_DIR / "agent_eval.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="跑 Agent 评估最低可用版")
    parser.add_argument("--print", action="store_true", help="只打印结果，不写文件")
    args = parser.parse_args()

    report = ev.run_all()
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=False)

    router = report["router"]
    schema = report["schema"]
    print(
        "Router  路线准确率={route_accuracy:.2%}  追问召回率={clarification_recall:.2%}  "
        "整体={overall_accuracy:.2%}  ({total_cases} 条)".format(**router)
    )
    print(f"Schema  通过率={schema['pass_rate']:.2%}  覆盖 {schema['agents_with_schema']} 个 Agent")
    if router["prompt_missing_routes"]:
        print(f"⚠ Router 提示词缺少路线：{router['prompt_missing_routes']}")

    # 门槛判定，方便当独立脚本跑时也能靠退出码看出成败
    ok = (
        router["route_accuracy"] >= ev.ROUTE_ACCURACY_MIN
        and router["clarification_recall"] >= ev.CLARIFICATION_RECALL_MIN
        and schema["pass_rate"] == ev.SCHEMA_PASS_RATE_REQUIRED
        and not router["prompt_missing_routes"]
    )

    if args.print:
        print(text)
    else:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        RESULTS_PATH.write_text(text + "\n", encoding="utf-8")
        print(f"已写入 {RESULTS_PATH}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
