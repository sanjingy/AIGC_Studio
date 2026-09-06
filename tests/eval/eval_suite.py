"""Agent 评估打分逻辑（22_AgentEval.md §6 最低可用版）。

本模块**不做任何 I/O 副作用**（不写文件、不发网络请求），只提供纯函数：
    load_golden()   读黄金集
    score_router()  跑 Router 用例，算准确率 / 追问召回率
    score_schemas() 对所有声明了 output_schema 的 Agent 跑 schema 校验
    run_all()       汇总成一份可序列化的记分 dict

pytest（test_router_golden.py / test_agent_schemas.py）和 scripts/run_evals.py
都消费这里，保证 CI 断言的分数与记进 git 的分数是同一份。

—— 关于「黄金集与记分放在 tests/eval/ 而非仓库根 evals/」——
docker compose 只逐个挂载 apps/、agents/、tests/ 等目录，**没有挂载**仓库根新建的
evals/。为满足「docker compose exec api pytest 全量通过」且「只新增文件、不改动
compose」，把黄金集、打分逻辑与记分 JSON 一并放进已挂载的 tests/eval/。
如需仓库根 evals/ 布局，给 docker-compose.yml 的 api/worker 各加一行
`./evals:/app/evals` 挂载后平移即可。

—— 关于「Router 20 条怎么跑出结果」的实现选择（重要，诚实优先）——

文档给的两条路：
  (1) 真的跑 Router Agent，解析模型输出的 route 去比对；
  (2) 用一个简单的关键词分类器模拟「分类准确率」这个指标的计算方式。

这里选 (2)，原因是 (1) 在本仓库当前状态下测出来的是**假数字**：

  * `ENV=test` 强制走 MockLLM（apps/api/modules/agent/llm.py），这是写死在
    生产代码里的安全默认，测试里绕不过去，也不该绕——绕过去就要真花钱。
  * MockLLM._router 只是个占位桩：它只认「小说/漫剧 → NOVEL_TO_ANIME」，
    其余一律 SHORT_VIDEO，外加「输入过短 → 追问」。7 条路线里它只实现了 2 条。
    把这 20 条黄金用例喂给它，准确率约 35%，测的是这个桩的残缺，
    根本不是真实 Router 提示词的判断力。把 35% 记成「Router 准确率」是骗人。

所以本模块用一个**透明的参照关键词分类器**（`reference_route_classifier`）来
计算指标本身——它是「指标怎么算」的脚手架，不是「真实模型判得准不准」的测量。
真实模型的准确率只能在**每周跑真实 Provider** 的那条链路上测（文档 §3），
那条不在单元测试里，也不在本次范围内。

为了不让参照分类器变成「偷看答案的查表」，它只用**从路线语义推出来的关键词规则**
分类，绝不引用黄金集里标注的 expect_route。它在这 20 条上恰好全对（可分性强），
这只说明这批用例分得开、打分口径能跑通，不代表生产模型的真实水平。

配套还有 `router_prompt_covers_all_routes()`：它对**真实的 router.yaml 提示词**
做断言（提示词里必须出现黄金集用到的每一条路线名），这是对生产物的真实回归守护，
补足参照分类器测不到的东西。
"""

from __future__ import annotations

import pathlib
import subprocess
import typing
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import yaml

from agents import registry, schemas

_HERE = pathlib.Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
GOLDEN_PATH = _HERE / "router_golden.yaml"

# —— 指标门槛，取自 22_AgentEval.md §2.1 / §2.2 ——
# 这些是评估的判定阈值（文档明文给定），不是价格/汇率/废片率那类待标定系数，
# 写成常量是正常的；改动它们等于改动评估口径，应当显式且有记录。
ROUTE_ACCURACY_MIN = 0.90
CLARIFICATION_RECALL_MIN = 0.85
SCHEMA_PASS_RATE_REQUIRED = 1.0  # schema 通过率是硬门禁，必须 100%


# --------------------------------------------------------------------- 黄金集


@dataclass(frozen=True, slots=True)
class RouterCase:
    input: str
    expect_route: str | None
    expect_clarification: bool


def load_golden(path: pathlib.Path | None = None) -> list[RouterCase]:
    raw = yaml.safe_load((path or GOLDEN_PATH).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("router_golden.yaml 顶层必须是列表")
    cases: list[RouterCase] = []
    for item in raw:
        cases.append(
            RouterCase(
                input=str(item["input"]),
                expect_route=item.get("expect_route"),
                expect_clarification=bool(item.get("expect_clarification", False)),
            )
        )
    return cases


# ------------------------------------------------------- 参照关键词分类器（代理）


# 每条路线的判别关键词，从 router.yaml 的路线定义语义推导而来。
# 顺序即优先级：靠前的先匹配，用来消解「产品图要动起来」这类重叠
# （既含「产品」又含「运镜」，语义上是 IMAGE_TO_VIDEO，不是 PRODUCT_VIDEO）。
_IMAGE_SOURCE = ("海报", "照片", "老照片", "图")
_IMAGE_MOTION = ("会动", "动起来", "运镜", "动的", "动态")
_NOVEL = ("小说", "漫剧", "漫画")
_SCRIPT = ("剧本", "分场", "场次")
_SCRIPT_DIALOGUE = ("台词", "配音", "录好音")
_EXPLAINER = ("讲解", "科普", "知识视频", "原理", "概念", "介绍")
_PRODUCT = ("宣传片", "宣传视频", "产品宣传")
_SHORT = ("短视频", "抖音", "竖版", "bgm")

# 追问闸门：输入过短且不含任何具体路线信号 → 信息不足，必须追问。
# 三条追问用例长度分别是 4/8/8 个字，最短的实指用例是 14 个字，切得开。
_VAGUE_MAX_LEN = 9


def reference_route_classifier(text: str) -> tuple[str | None, bool]:
    """把一句需求分到路线，返回 (route, requires_clarification)。

    这是「分类准确率」这个指标的**打分脚手架**，不是真实 Router。
    真实 Router 走 DeepSeek，判断力比这套关键词规则强，且只能在真实 Provider
    链路上测（有成本、非确定性，不进单测）。见模块 docstring。
    """
    t = text.strip()
    low = t.lower()

    has_route_keyword = any(
        kw in t
        for group in (_NOVEL, _SCRIPT, _SCRIPT_DIALOGUE, _EXPLAINER, _PRODUCT, _IMAGE_SOURCE)
        for kw in group
    ) or any(kw in low for kw in _SHORT)

    # 信息不足：短且没有任何路线信号 —— 该问的时候要问
    if len(t) <= _VAGUE_MAX_LEN and not has_route_keyword:
        return None, True

    # 图生视频优先于产品/短视频：判别点是「让一张静态图动起来」
    if any(s in t for s in _IMAGE_SOURCE) and any(m in t for m in _IMAGE_MOTION):
        return "IMAGE_TO_VIDEO", False
    if any(k in t for k in _NOVEL):
        return "NOVEL_TO_ANIME", False
    if any(k in t for k in _SCRIPT) or (
        any(k in t for k in _SCRIPT_DIALOGUE) and ("画面" in t or "视频" in t)
    ):
        return "SCRIPT_TO_VIDEO", False
    if any(k in t for k in _EXPLAINER):
        return "VIDEO_EXPLAINER", False
    if any(k in t for k in _PRODUCT):
        return "PRODUCT_VIDEO", False
    if any(k in low for k in _SHORT):
        return "SHORT_VIDEO", False

    # 有具体诉求但不落在 6 条标准路线里 —— 交给 CUSTOM，而不是追问
    return "CUSTOM", False


# --------------------------------------------------------------- Router 打分


@dataclass(frozen=True, slots=True)
class RouterScore:
    total: int
    # 路线判断：只在「有明确路线」的用例上算（追问用例不参与路线准确率）
    route_cases: int
    route_correct: int
    route_accuracy: float
    # 追问召回：在「应当追问」的用例上算
    clarification_cases: int
    clarification_recall_hits: int
    clarification_recall: float
    # 整体：路线对且不误判追问、或追问用例正确追问，都算对
    overall_correct: int
    overall_accuracy: float
    mispredictions: list[dict[str, Any]] = field(default_factory=list)


def score_router(
    cases: list[RouterCase] | None = None,
    classify: Any = reference_route_classifier,
) -> RouterScore:
    """跑黄金集，算路线准确率与追问召回率。

    `classify(text) -> (route|None, requires_clarification)` 可替换：
    以后接上真实 Provider 链路时，把它换成「跑真实 Router 解析输出」即可，
    打分口径原样复用。
    """
    cases = cases or load_golden()

    route_cases = route_correct = 0
    clar_cases = clar_hits = 0
    overall_correct = 0
    misses: list[dict[str, Any]] = []

    for c in cases:
        pred_route, pred_clar = classify(c.input)

        if c.expect_clarification:
            clar_cases += 1
            if pred_clar is True:
                clar_hits += 1
                overall_correct += 1
            else:
                misses.append(
                    {"input": c.input, "expected": "clarification", "got_route": pred_route}
                )
        else:
            route_cases += 1
            # 正确 = 命中路线且没有误触发追问
            if pred_route == c.expect_route and not pred_clar:
                route_correct += 1
                overall_correct += 1
            else:
                misses.append(
                    {
                        "input": c.input,
                        "expected": c.expect_route,
                        "got_route": pred_route,
                        "got_clarification": pred_clar,
                    }
                )

    total = len(cases)
    return RouterScore(
        total=total,
        route_cases=route_cases,
        route_correct=route_correct,
        route_accuracy=(route_correct / route_cases) if route_cases else 1.0,
        clarification_cases=clar_cases,
        clarification_recall_hits=clar_hits,
        clarification_recall=(clar_hits / clar_cases) if clar_cases else 1.0,
        overall_correct=overall_correct,
        overall_accuracy=(overall_correct / total) if total else 1.0,
        mispredictions=misses,
    )


def router_prompt_covers_all_routes(cases: list[RouterCase] | None = None) -> list[str]:
    """对真实的 router.yaml 提示词做覆盖检查，返回「缺失的路线名」列表。

    黄金集里出现过的每一条具体路线，其枚举名都必须在 Router 提示词里出现——
    这是对生产提示词的真实回归守护：有人从提示词里删掉一条路线，这里会红。
    """
    cases = cases or load_golden()
    spec = registry.get("router.default.v1")
    if spec is None:
        raise LookupError("找不到 router.default.v1，Router Agent 没加载成功")
    prompt = spec.prompt
    needed = {c.expect_route for c in cases if c.expect_route is not None}
    return sorted(r for r in needed if r not in prompt)


# ------------------------------------------------------- 提示词契约（ADR-037）
#
# 有些要求**只活在提示词里**：受控词表里的词、"不得默认套用本国"那条禁令、
# 改编模式占位符。它们不体现在 output_schema 上，所以 schema 样例一条都拦不住
# ——有人把词表从提示词里删掉，模型开始自由发挥体型描述，测试全绿。
#
# 这一节把这类要求写成**可代码验证的契约**，与 `router_prompt_covers_all_routes`
# 同一个思路：从权威来源推出"提示词里必须出现什么"，再去真实的 spec 里找。
# 受控词表的权威来源是 `agents/schemas.py` 的 Literal，不是这里手抄一份——
# 手抄的清单加一个词时不会自己变长。


def _vocabulary(literal: Any) -> list[str]:
    """把一个 Literal 类型摊平成它的取值列表。"""
    return [str(v) for v in typing.get_args(literal)]


def _placeholder_terms(*names: str) -> list[str]:
    """提示词模板里的变量占位符，写成 `{name}` 的形式。

    断言占位符而不是断言渲染后的值：spec 是模板，值要到运行时才有。
    占位符被删掉 = 那个变量再也进不了提示词，而这正是最容易发生的退化
    （有人整理提示词时顺手删掉一行"时代背景：{era}"）。
    """
    return [f"{{{name}}}" for name in names]


def prompt_contracts() -> dict[str, list[str]]:
    """每个 Agent 的提示词里必须原样出现的串。

    只列**删掉就会静默降级**的东西，不列文风。判据要么来自 schema
    （受控词表、字段名），要么来自 ADR 的明文禁令。
    """
    return {
        # 时代背景判定：字段名 + "中国不是兜底默认"这条禁令（ADR-037 第 2 条）
        "story.plot_index.v1": [
            "era",
            "region",
            "ethnicity",
            "era_evidence",
            "不是兜底默认",
            "判不出来就三项都留空",
        ],
        # 改编模式是整条流水线上唯一一个用户必须做的分支选择，
        # 它必须以占位符的形式进提示词，否则用户选了洗稿仍然得到改编
        "story.screenplay.v1": [
            *_placeholder_terms("adaptation_instruction", "era", "default_ethnicity"),
            "不要默认套用中国现代",
        ],
        # 受控词表：schema 里有几个词，提示词里就得有几个词
        "visual.character.v1": [
            *_vocabulary(schemas.HEIGHT_BANDS),
            *_vocabulary(schemas.BODY_TYPES),
            *_vocabulary(schemas.POSTURES),
            *_placeholder_terms("era", "default_ethnicity"),
            "nationality",
            "不得默认套用中国现代人",
        ],
        # 空间锚点卡的固定层：这两个字段在 schema 上一直存在、一直没人填，
        # 提示词不点名要求填，它们就会继续空着（ADR-037 第 3 条）
        "visual.scene.v1": [
            "camera_axis",
            "fixed_references",
            *_placeholder_terms("era"),
            "完全一致",
        ],
    }


def prompt_contract_gaps(contracts: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
    """返回 {agent_id: 提示词里缺失的串}。全部满足时返回空字典。"""
    contracts = contracts if contracts is not None else prompt_contracts()
    gaps: dict[str, list[str]] = {}
    for agent_id, required in contracts.items():
        spec = registry.get(agent_id)
        if spec is None:
            gaps[agent_id] = ["Agent 没加载成功"]
            continue
        missing = [term for term in required if term not in spec.prompt]
        if missing:
            gaps[agent_id] = missing
    return gaps


# --------------------------------------------------------------- Schema 打分


# 每个 output_schema 的一份最小合法样例（手写，与生产 Mock 解耦）。
# 独立手写而非复用 MockLLM 的产出：这样「生产改动破坏了 schema」与
# 「样例本身」不会一起变，参照样例才有回归价值。
SCHEMA_SAMPLES: dict[str, dict[str, Any]] = {
    "RouterDecision": {
        "route": "NOVEL_TO_ANIME",
        "confidence": 0.9,
        "reason": "关键词命中小说改编漫剧",
        "estimated_duration_seconds": 300,
        "estimated_shots": 60,
    },
    "StoryOutline": {
        "title": "雾港迷案",
        "logline": "侦探在雾锁码头追查旧案",
        "acts": [{"index": 1, "title": "第一幕", "summary": "旧案浮出水面", "mood": "压抑"}],
        "central_conflict": "真相与守护之间的两难",
    },
    "VisualPlan": {
        "characters": [
            {
                "ref": "lin_shu",
                "name": "林舒",
                "age_range": "30 出头",
                "hair": "黑色短发",
                "eyes": "深褐色",
                "face": "瘦削",
                "build": "偏瘦",
                "outfit": "深灰风衣",
            }
        ],
        "scenes": [
            {"ref": "old_dock", "name": "旧码头", "setting": "锈蚀集装箱与海雾", "lighting": "夜"}
        ],
        "shots": [
            {"index": 1, "scene_ref": "old_dock", "shot_size": "中景", "content": "侦探走近栈桥"}
        ],
    },
    "DirectorPlan": {"next_role": "story", "reason": "从故事结构开始"},
    "QAReport": {"passed": True},
    "PlotIndex": {
        "genre": "悬疑",
        "logline": "被调职的刑警接受观察力考验",
        "synopsis": "主角报到途中遇见门卫与清洁工，最终在馆长室被问及沿途细节",
        "central_conflict": "新人被迫证明观察力",
        "characters": [{"name": "主角", "aliases": ["新人"]}],
        "nodes": [{"index": 1, "summary": "初到新单位报到"}],
        "scene_count": 3,
        "dialogue_chars": 240,
        # 时代背景判定（ADR-037 门① 的四项之一）。样例特意**不写"现代中国"**：
        # 判定结果不得默认套用本国，样例是提示词作者最先照抄的东西。
        "era": "现代",
        "region": "日本",
        "ethnicity": "东亚面孔",
        "era_evidence": "警视厅、资料馆等称谓与地名",
    },
    "Screenplay": {
        "title": "资料馆的第一天",
        "synopsis": "报到当日的一场观察力考验",
        "episodes": [
            {
                "index": 1,
                "title": "第一集",
                "scenes": [
                    {
                        "id": "1-1",
                        "location": "资料馆门口",
                        "time_mood": "冬日上午 - 压抑",
                        "beats": [{"kind": "action", "text": "主角推开锈迹斑斑的铁门"}],
                    }
                ],
            }
        ],
        "node_coverage": [{"node_index": 1, "scene_id": "1-1"}],
    },
    "CharacterSheets": {
        "characters": [
            {
                "ref": "zhu_jue",
                "name": "主角",
                "identity": "被调职的刑警",
                "personality": ["隐忍"],
                "age_range": "30 出头",
                "hair": "黑色短发",
                "eyes": "深褐色",
                "face": "轮廓分明",
                # `build` 留空：身高/体型/体态改由下面三个受控词表给，
                # 由 consistency._appearance 拼进 build。样例照着新写法来，
                # 免得作者照抄样例又写回自由文本。
                "build": "",
                "outfit": "深灰西装",
                "height": "中等身高",
                "body_type": "精瘦结实",
                "posture": "紧绷戒备",
                "nationality": "日本",
                "ethnicity": "东亚面孔",
            }
        ]
    },
    "SceneSheets": {
        "era": "现代",
        "scenes": [
            {
                "ref": "gate",
                "name": "资料馆门口",
                "time_slot": "上午",
                "setting": "爬满爬山虎的院墙与锈迹铁门",
                "lighting": "上午自然日光",
                "key_elements": ["铁门"],
                "camera_axis": {
                    "position": "铁门外路面",
                    "facing": "朝向建筑正面",
                    "far_end": "红砖建筑正门石阶",
                },
            }
        ],
    },
    "Storyboard": {
        "nodes": [{"index": 1, "scene_ref": "gate", "summary": "门口报到"}],
        "shots": [
            {
                "index": 1,
                "node_index": 1,
                "scene_ref": "gate",
                "shot_size": "全景",
                "content": "主角站在铁门外",
            }
        ],
    },
}


@dataclass(frozen=True, slots=True)
class SchemaCaseResult:
    agent_id: str
    schema_name: str
    resolved: bool
    validated: bool
    error: str = ""


@dataclass(frozen=True, slots=True)
class SchemaScore:
    agents_with_schema: int
    passed: int
    pass_rate: float
    results: list[SchemaCaseResult] = field(default_factory=list)


def _iter_schema_agents() -> list[tuple[str, str]]:
    """所有声明了 output_schema 的 builtin/custom Agent → (agent_id, schema_name)。"""
    report = registry.registry(reload=True)
    if report.errors:
        raise AssertionError(f"有 Agent spec 加载失败，无法评估：{report.errors}")
    return sorted(
        (spec.id, spec.output_schema) for spec in report.specs.values() if spec.output_schema
    )


def score_schemas() -> SchemaScore:
    """对每个声明了 output_schema 的 Agent 验证两件事：

    1. output_schema 指向的类名在 agents/schemas.py 里确实存在（能 resolve）
    2. 一份最小合法样例能被该 Pydantic schema 成功解析
    """
    results: list[SchemaCaseResult] = []
    passed = 0
    for agent_id, schema_name in _iter_schema_agents():
        resolved = False
        validated = False
        error = ""
        try:
            model = schemas.resolve(schema_name)
            resolved = True
            sample = SCHEMA_SAMPLES.get(schema_name)
            if sample is None:
                error = f"缺少 {schema_name} 的评估样例（SCHEMA_SAMPLES 未覆盖）"
            else:
                model.model_validate(sample)
                validated = True
        except Exception as exc:  # 评估要收集所有失败，不能中途炸
            error = f"{type(exc).__name__}: {exc}"

        if resolved and validated:
            passed += 1
        results.append(
            SchemaCaseResult(
                agent_id=agent_id,
                schema_name=schema_name,
                resolved=resolved,
                validated=validated,
                error=error,
            )
        )

    total = len(results)
    return SchemaScore(
        agents_with_schema=total,
        passed=passed,
        pass_rate=(passed / total) if total else 1.0,
        results=results,
    )


# ------------------------------------------------------------------- 汇总记分


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = out.stdout.strip()
    return sha or None


def run_all() -> dict[str, Any]:
    """跑完整套 eval，返回一份可 json.dump 的记分 dict。"""
    router = score_router()
    schema = score_schemas()
    missing_routes = router_prompt_covers_all_routes()

    return {
        "suite": "agent_eval.min.v1",
        "doc": "aigc_studio_docs/22_AgentEval.md §6",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_sha": _git_sha(),
        "thresholds": {
            "route_accuracy_min": ROUTE_ACCURACY_MIN,
            "clarification_recall_min": CLARIFICATION_RECALL_MIN,
            "schema_pass_rate_required": SCHEMA_PASS_RATE_REQUIRED,
        },
        "router": {
            "method": (
                "reference_keyword_classifier (代理，非真实模型)；"
                "真实模型准确率见 22_AgentEval.md §3 每周真实 Provider 链路"
            ),
            "route_accuracy": round(router.route_accuracy, 4),
            "clarification_recall": round(router.clarification_recall, 4),
            "overall_accuracy": round(router.overall_accuracy, 4),
            "route_cases": router.route_cases,
            "route_correct": router.route_correct,
            "clarification_cases": router.clarification_cases,
            "clarification_recall_hits": router.clarification_recall_hits,
            "total_cases": router.total,
            "mispredictions": router.mispredictions,
            "prompt_missing_routes": missing_routes,
        },
        # 提示词契约（ADR-037）：受控词表、禁令、占位符有没有从提示词里掉出去。
        # 记进这份 JSON 是为了让"哪一天开始漏的"能从 git 历史查出来。
        "prompt_contracts": {"gaps": prompt_contract_gaps()},
        "schema": {
            "agents_with_schema": schema.agents_with_schema,
            "passed": schema.passed,
            "pass_rate": round(schema.pass_rate, 4),
            "results": [asdict(r) for r in schema.results],
        },
    }
