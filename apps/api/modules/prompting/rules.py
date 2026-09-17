"""成品提示词的规则与校验器。**纯函数，不碰数据库，不调模型。**

ADR-036 把"最终提示词由谁写"从确定性代码翻到了 Agent 身上，同时留下一条
必须由平台强制的约束：

    风格词仍然由系统提供，且必须**逐字展开**；Agent 不得改写、精简、
    翻译或替换。缺失或被改写时判定为输出不合格，不静默放行。

这个模块就是那条约束的落点，外加每一类模板自己的硬性要求（四视图必须真的
是四格、五要素一个都不能少、视频提示词的最后一行不可省略）。

**为什么校验器在这里而不在 `agents/schemas.py`**：pydantic 那一层看不到
项目锁定的画风——它只认输出本身的形状。"这段文字里有没有原样保留那几个
风格词"必须拿着 `style_profiles` 那一行才判得了，而那是运行时的数据。

**为什么不退回旧的确定性拼接兜底**：`compose.compose_shot` 还在，但它已经
不在生产出图路径上。判不合格就报错、就不建任务——用旧拼接"修好"一次，
用户拿到的是一张与全片画风无关的图，而且没有任何地方会告诉他这件事发生过。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

# 规则版本。写进每一条提示词运行与每一个出图任务，用来回答
# "这张图是按哪一版模板生成的"。模板（超哥 Skill 的 B/C 两份原文）或本文件
# 的校验规则发生实质变化时递增，**不随代码提交递增**——它标的是规则不是版本。
RULE_VERSION = "chaoge-1.1/adr036-20260916"

KIND_CHARACTER = "character"
KIND_SCENE = "scene"
KIND_SHOT_IMAGE = "shot_image"
KIND_SHOT_VIDEO = "shot_video"

#: 四类成品提示词。与 `agent_runs.input_json.prompt_kind` 的取值一一对应。
KINDS: tuple[str, ...] = (KIND_CHARACTER, KIND_SCENE, KIND_SHOT_IMAGE, KIND_SHOT_VIDEO)

#: 每一类由哪个 Agent 产出。钉死到具体 id 而不是 `default_for("visual")`——
#: visual 这个 role 下现在有 7 个 Agent，按 role 取会取到角色档案那一个。
AGENT_OF: dict[str, str] = {
    KIND_CHARACTER: "visual.character_prompt.v1",
    KIND_SCENE: "visual.scene_prompt.v1",
    KIND_SHOT_IMAGE: "visual.shot_frame_prompt.v1",
    KIND_SHOT_VIDEO: "visual.shot_video_prompt.v1",
}

#: 界面上的中文名。生成记录的标题按它拼，用户不必学 B3 / B5 这套编号。
KIND_LABEL: dict[str, str] = {
    KIND_CHARACTER: "角色立绘提示词",
    KIND_SCENE: "场景四视图提示词",
    KIND_SHOT_IMAGE: "首帧提示词",
    KIND_SHOT_VIDEO: "视频提示词",
}

#: 视频提示词代码块的最后一行。原 Skill C 阶段全局约束第 11 条：
#: 不可省略、不可改写、不可移到代码块外侧。
VIDEO_TAIL = "只添加音效和台词，禁止添加背景音乐，禁止出现字幕。"

#: B5 四格的格名。顺序固定，四个都要在提示词里出现。
QUADRANT_LABELS: tuple[tuple[str, str], ...] = (
    ("top_left", "左上格"),
    ("top_right", "右上格"),
    ("bottom_left", "左下格"),
    ("bottom_right", "右下格"),
)

#: 人物排除的三重否定（原 Skill 模板 B 坑三）。三句分别封堵"画出人"、
#: "描述动作"、"隐含人存在"三条路径，缺一不可——只写一句实测仍会漏进人影。
NO_PEOPLE_CLAUSES: tuple[str, ...] = (
    "禁止出现任何人物或群体人物",
    "禁止描写人物动作",
    "画面中不得有任何人",
)

#: 景别词库里需要补局部说明的两个。局部说明**直接替换**"特写"二字，
#: 所以校验按后缀匹配而不是相等：分镜写「特写」，成品词写「手部特写」是对的。
DETAIL_SHOT_SIZES: tuple[str, ...] = ("极近特写", "特写")

_SPLIT = re.compile(r"[，,、]")


def split_style_tokens(tokens: str) -> list[str]:
    """把一串风格描述词拆成逐个词。

    按中英文逗号和顿号切。切开再逐个查，而不是要求整段原样出现：模型把
    风格词写在句首、后面接一个中文句号或者另起一句，是完全正确的写法，
    但整段匹配会把它判成不合格。**逐个词匹配抓的才是真正的退化**——
    改写、精简、翻译、替换，这四种做法里任何一种都会让至少一个词对不上。
    """
    return [t for raw in _SPLIT.split(tokens or "") if (t := raw.strip())]


def missing_style_tokens(prompt: str, tokens: str) -> list[str]:
    """风格词里有哪些没被原样保留。全部保留时返回空列表。"""
    text = prompt or ""
    return [t for t in split_style_tokens(tokens) if t not in text]


def _missing_phrases(prompt: str, phrases: Sequence[str]) -> list[str]:
    return [p for p in phrases if p and p not in prompt]


def _strip_label(text: str, label: str) -> str:
    """去掉自检字段里可能带上的格名前缀（「左上格：…」）。

    模型很自然会把格名连同描述一起填进字段，此时拿它去 prompt 里找整段就
    会多出一个冒号对不上。这不是退化，不该判不合格。
    """
    cleaned = (text or "").strip()
    for prefix in (f"{label}：", f"{label}:", label):
        if cleaned.startswith(prefix):
            return cleaned[len(prefix) :].strip()
    return cleaned


def check_style(prompt: str, *, style: Mapping[str, Any]) -> list[str]:
    """风格词与全局色调有没有被原样保留。返回中文问题列表。"""
    problems: list[str] = []
    if missing := missing_style_tokens(prompt, str(style.get("tokens", ""))):
        joined = "、".join(missing)
        problems.append(
            f"锁定画风的这些描述词没有原样保留：{joined}"
            "（ADR-036：风格词由系统提供，必须逐字照抄，不得改写或精简）"
        )
    for field, label in (("color_grading", "全局色调"), ("render_mode", "渲染方式")):
        value = str(style.get(field, "") or "").strip()
        if value and value not in prompt:
            problems.append(f"{label}「{value}」没有写进提示词")
    return problems


#: 角色档案里"这个人是谁"可能落在哪几个键上。顺序即优先级。
#: 与 `context._character_basis` 判"缺不缺身份"用的是**同一份定义**——
#: 抄成两份，迟早出现"组装时认为有、校验时认为没有"。
IDENTITY_KEYS: tuple[str, ...] = ("nationality", "ethnicity")

#: 关键配件可能落在哪几个键上。同样顺序即优先级。
ACCESSORY_KEYS: tuple[str, ...] = ("accessories", "key_accessories", "props")


def _as_text(raw: Any) -> str:
    """把档案上的一个值规范成一段文字。列表按顿号拼，其余按 str 取。"""
    if isinstance(raw, list):
        raw = "、".join(str(x).strip() for x in raw if str(x).strip())
    return str(raw or "").strip()


def _first_text(sources: Sequence[Mapping[str, Any]], keys: Sequence[str]) -> str:
    for source in sources:
        for key in keys:
            if value := _as_text(source.get(key)):
                return value
    return ""


def known_identity(character: Mapping[str, Any], era: Mapping[str, Any] | None = None) -> str:
    """档案（或门① 锁定的时代背景）已经确定的国籍/人种。不知道就空串。"""
    sources: list[Mapping[str, Any]] = [character, dict(character.get("appearance") or {})]
    if era:
        sources.append(era)
    return _first_text(sources, IDENTITY_KEYS)


def known_accessories(character: Mapping[str, Any]) -> str:
    """档案已经写明的关键配件。没写就空串（**不是**「无」）。

    "档案没写"和"档案写了没有"是两件事：前者模型可以自己判断，后者是
    确定的事实。混成一个值会让校验器要么放过漏配件，要么要求提示词里
    出现「无」这两个字。
    """
    sources: list[Mapping[str, Any]] = [character, dict(character.get("appearance") or {})]
    return _first_text(sources, ACCESSORY_KEYS)


#: 档案上"这个人长什么样"的结构化事实，逐项对应模板 A 的一个要素槽位
#: （国籍人种 → 年龄体型 → 五官 → 发型 → 肤色 → 服装 → 鞋子 → 配件 → 标志性细节）。
#:
#: **为什么校验这些而不是只校验 `subject_identity` / `accessories`**：那两个是
#: 模型**自报**的字段，它可以把发型写成"短发"、把"深灰西装外套，白衬衫，无领带"
#: 精简成"西装"，自报字段一个字不动，校验全绿——而这个角色已经换了个人。
#: 立绘是后续每一镜的比对基准，基准上少一件衣服，全片都跟着少。
#:
#: 取值来源**以冻结的 `appearance_json` 为准**，顶层 sheet 只是回落，见
#: `_appearance_fact`。两份不一致时听冻结的那一份：出图与相似度比对用的就是它。
HUMAN_APPEARANCE_FACTS: tuple[tuple[str, str], ...] = (
    ("age_range", "年龄段"),
    ("hair", "发型发色"),
    ("eyes", "眼睛"),
    ("face", "五官轮廓"),
    ("build", "身高体型体态"),
    ("outfit", "服装造型"),
    ("skin", "肤色"),
    ("shoes", "鞋子"),
    ("distinctive", "标志性细节"),
)

#: 非人类角色只查模板 B 真的有槽位的那几项。拿"鞋子""发型"去要求一头狼，
#: 得到的只会是一条永远不合格的规则，而两套模板不得混用是 B3 的执行铁律。
NON_HUMAN_APPEARANCE_FACTS: tuple[tuple[str, str], ...] = (
    ("build", "体型"),
    ("skin", "体表"),
    ("distinctive", "标志性特征"),
)

#: 人类角色面部四铁律点名禁止的道具。既用来查成品词，也用来查档案本身——
#: 档案上写着背包时，"照抄档案"和"严禁背包"是两条互相矛盾的指令。
BANNED_ITEMS: tuple[str, ...] = ("背包", "书包")

#: "档案写了没有"的几种写法。它们是**确定的事实**而不是待写进画面的东西，
#: 要求提示词里出现「无」这两个字只会逼出一句废话。
NOT_APPLICABLE: tuple[str, ...] = ("无", "无道具", "没有", "不适用")

#: 摄影主轴的三段。顺序即"机位怎么摆"的叙述顺序。
AXIS_FIELDS: tuple[tuple[str, str], ...] = (
    ("position", "摄影机站位"),
    ("facing", "朝向"),
    ("far_end", "远景末端"),
)

#: 主轴被点明为构图依据时会出现的词。四视图的"正面"必须由主轴决定，
#: 只把站位当背景描述写进去是不够的——那样模型仍然自己挑角度。
AXIS_BASIS_PHRASES: tuple[str, ...] = ("摄影主轴", "主轴")


def _appearance_fact(character: Mapping[str, Any], key: str) -> str:
    """某一项结构化外貌事实的当前取值。**冻结的那一份说了算。**

    `character["appearance"]` 是 `character_profiles.appearance_json`——
    `consistency._appearance` 冻下来、出图与相似度比对真正用的那一版。
    顶层来自 `current_state_json` 里的整份 sheet，上游重跑一次就会变，
    而冻结的那份不会（`sync_from_characters_output` 对已锁定的角色不覆盖）。

    两边不一致时听冻结的：否则校验会去要求提示词里出现一段**这张图根本不会
    照着画**的旧事实，而模型照着新档案写反倒被判不合格。

    键存在但值为空 = 冻结时这一项就是空的，**不回落**把旧 sheet 上的值复活；
    键根本不存在（存量档案、或非人类模板才有的那几项）才回落到顶层。
    """
    frozen = character.get("appearance")
    if isinstance(frozen, Mapping) and key in frozen:
        return _as_text(frozen[key])
    return _as_text(character.get(key))


def known_appearance(character: Mapping[str, Any], template: str = "人类") -> list[tuple[str, str]]:
    """档案已经确定的结构化外貌事实，`[(标签, 值)]`。没写的不在列表里。"""
    keys = HUMAN_APPEARANCE_FACTS if template == "人类" else NON_HUMAN_APPEARANCE_FACTS
    return [(label, value) for key, label in keys if (value := _appearance_fact(character, key))]


def _fact_problems(prompt: str, facts: Sequence[tuple[str, str]], *, human: bool) -> list[str]:
    """已知事实逐条查成品词。**判据是上下文，不是模型的自检字段。**

    每条事实按中文分隔符拆开再逐项查，与 `split_style_tokens` 同一个理由：
    "深灰西装外套，白衬衫，无领带"被精简成"深灰西装"时，整段匹配和逐项匹配
    的区别就是抓不抓得住——而被丢掉的那件白衬衫下一镜就会变成别的颜色。

    档案本身与模板硬约束冲突时**显式报错**，不静默取舍：既不偷偷放行一个
    带背包的立绘（四铁律是实测出来的，背包会把人物比例带偏），也不偷偷把
    档案上的事实删掉当没看见（那是在替用户改他的角色设定）。要修的是档案，
    而只有他能修。
    """
    problems: list[str] = []
    for label, value in facts:
        for item in split_style_tokens(value):
            if not item or item in NOT_APPLICABLE:
                continue
            if human and (banned := next((b for b in BANNED_ITEMS if b in item), None)):
                problems.append(
                    f"角色档案的{label}写着「{item}」，与人类角色面部四铁律「严禁{banned}」"
                    "冲突：照抄档案就违反模板，按模板删掉又等于替用户改角色设定。"
                    "请先修正角色档案里的这一项再出图"
                )
                continue
            if item not in prompt:
                problems.append(
                    f"档案上的{label}「{item}」没有原样出现在提示词里"
                    "（立绘是后续每一镜的比对基准，基准上少一项，全片都跟着少）"
                )
    return problems


def _axis_problems(
    prompt: str,
    scene: Mapping[str, Any],
    fields: Sequence[tuple[str, str]],
    *,
    basis_required: bool,
) -> list[str]:
    """摄影主轴有没有活到成品词里。空值不查——档案没写就是没写。"""
    problems: list[str] = []
    axis = dict(scene.get("camera_axis") or {})
    for key, label in fields:
        value = str(axis.get(key, "") or "").strip()
        if value and value not in prompt:
            problems.append(
                f"场景摄影主轴的{label}「{value}」没有写进提示词"
                "（主轴是这个场景空间一致性的全部依据，丢了它同一个房间两次生成会朝两个方向）"
            )
    if basis_required and axis and not any(p in prompt for p in AXIS_BASIS_PHRASES):
        problems.append(
            "提示词没有把摄影主轴点明为构图依据"
            "（只把站位当背景描述写进去不够，正视图的「正面」必须由主轴决定）"
        )
    return problems


def _anchor_problems(prompt: str, source: Mapping[str, Any]) -> list[str]:
    """固定参照物的名称与描述有没有活到成品词里。**每一条都要，不打折。**

    名称给模型"画的是什么东西"，描述给它"长什么样、在哪"。只留名称，
    "门柱铜牌"这镜在左下镜在右——那正是空间锚点要防的东西。

    四视图与单镜首帧**同一个要求**：完整的参照事实是这个空间的**参照系**，
    写全它不等于要求每一件参照物都出现在本镜画面里。哪一部分入画由分镜表的
    景别与角度决定，而那两项另有校验。放松成"至少落地一条"看似温和，实际是
    让模型自己挑留哪几条——它挑剩下的那几条下一镜就会换个样子，于是同一个
    房间又开始漂。
    """
    refs = [r for r in (source.get("fixed_references") or []) if isinstance(r, dict)]
    problems: list[str] = []
    for ref in refs:
        name = str(ref.get("name", "") or "").strip()
        description = str(ref.get("description", "") or "").strip()
        if name and name not in prompt:
            problems.append(
                f"空间锚点「{name}」的名称没有写进提示词（元素锁定清单少一项，这一格就和别格对不上）"
            )
        if description and description not in prompt:
            problems.append(
                f"空间锚点「{name or description[:8]}」的描述没有写进提示词"
                "（只给名称模型不知道它长什么样、在哪，两镜之间就会挪位置）"
            )
    return problems


def _lighting_problems(prompt: str, context: Mapping[str, Any]) -> list[str]:
    """这一次该用的光照状态在不在，不该用的有没有漏进来。

    后半条不是洁癖：把场景声明的全部状态一起写进去，模型看到的是互相矛盾的
    指令（"上午"和"傍晚"同时成立），结果只会是它自己挑一个——那和没有这个
    字段一样。
    """
    problems: list[str] = []
    lighting = dict(context.get("lighting") or {})
    used = str(lighting.get("description", "") or "").strip()
    if used and used not in prompt:
        problems.append(
            f"这一镜实际使用的光照状态「{lighting.get('name') or used}」的描述没有写进提示词"
        )
    scene = dict(context.get("scene") or {})
    for state in scene.get("lighting_states") or []:
        if not isinstance(state, dict):
            continue
        description = str(state.get("description", "") or "").strip()
        if description and description != used and description in prompt:
            problems.append(
                f"没有被引用的光照状态「{state.get('name')}」也写进了提示词"
                "（两种光同时成立时模型只会自己挑一个）"
            )
    return problems


def _check_character(output: Mapping[str, Any], context: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    character = dict(context.get("character") or {})
    prompt = str(output.get("prompt", ""))

    template = "人类" if str(character.get("kind", "人类")) == "人类" else "非人类"
    if str(output.get("template", "")) != template:
        problems.append(
            f"角色类型是「{character.get('kind')}」，应当用{template}模板，"
            f"实际用了「{output.get('template')}」模板（两套模板不得混用）"
        )

    identity = str(output.get("subject_identity", "")).strip()
    if identity and identity not in prompt:
        problems.append(f"国籍/人种（或物种）「{identity}」没有写进提示词")

    # 只查模型自报的那一项是不够的：它可以把档案上的「中国」改成「东亚」，
    # 自报一致、提示词里也确实有那个词，校验全绿，而角色的身份已经被换掉了。
    # 所以拿**上下文已知的值**再比一次（ADR-037 第 2 条：判定结果不得被改写）。
    known = known_identity(character, context.get("era") or {})
    if known:
        if known not in prompt:
            problems.append(
                f"档案上的国籍/人种「{known}」没有原样出现在提示词里"
                "（它决定五官、服装、发型，被改写会一路传导到全片）"
            )
        if identity and known not in identity and identity not in known:
            problems.append(f"提示词自报的身份是「{identity}」，档案上是「{known}」，两者对不上")

    accessories = str(output.get("accessories", "")).strip()
    if accessories and accessories != "无" and accessories not in prompt:
        problems.append(
            f"关键配件「{accessories}」没有写进提示词"
            "（配件往往是这个角色的识别度所在，不能只填在自检字段里）"
        )

    # 同理：档案里写了配件，模型却一律填「无」——这是实测最常见的退化，
    # 而它抹掉的往往正是这个角色的识别物（钥匙串、腰牌、眼镜）。
    human = template == "人类"
    problems += _fact_problems(prompt, [("关键配件", known_accessories(character))], human=human)

    # 档案上已经确定的结构化外貌事实（发型、服装、鞋子、标志性细节……）逐项
    # 必须活到成品词里。**只查 `subject_identity` / `accessories` 两个自报字段
    # 是不够的**：模型可以把「深灰西装外套，白衬衫，无领带」精简成「西装」，
    # 两个自报字段一个字不动、校验全绿，而这个角色已经换了身衣服——而这张
    # 立绘正是后续每一镜的比对基准。
    problems += _fact_problems(prompt, known_appearance(character, template), human=human)

    if human:
        for banned in BANNED_ITEMS:
            if banned in prompt:
                problems.append(
                    f"立绘提示词里出现了「{banned}」（人类角色面部四铁律之一：严禁背包书包）"
                )
        if "无任何表情" not in prompt:
            problems.append("立绘提示词缺少「无任何表情」——基准立绘带情绪就当不了一致性基准")
    return problems


def _check_scene(output: Mapping[str, Any], context: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    prompt = str(output.get("prompt", ""))
    quadrants = dict(output.get("quadrants") or {})
    # 上下文**不能丢**：`fixed_elements` 是模型自己列的清单，拿它去查 prompt
    # 只能证明"它写的东西它自己抄了一遍"。档案上真正钉死这个空间的是摄影主轴
    # 和固定参照物，而那两样只有 context 里有。
    scene = dict(context.get("scene") or {})

    if not any(g in prompt for g in ("2x2", "2×2", "2X2")):
        problems.append("场景概念图提示词没有声明 2x2 网格布局")

    for field, label in QUADRANT_LABELS:
        if label not in prompt:
            problems.append(f"提示词里缺少「{label}」这一格")
            continue
        described = _strip_label(str(quadrants.get(field, "")), label)
        if described and described not in prompt:
            problems.append(f"「{label}」的画面描述没有写进提示词，自检字段与正文对不上")

    if missing := _missing_phrases(prompt, NO_PEOPLE_CLAUSES):
        joined = "、".join(missing)
        problems.append(
            f"人物排除的三重否定缺了：{joined}"
            "（只写一句实测仍会漏进人影，而场景基准图里有人就当不了基准）"
        )

    if "无文字标注" not in prompt:
        problems.append("提示词缺少「无文字标注」——AI 生成的文字几乎必然是乱码")

    elements = [str(e).strip() for e in (output.get("fixed_elements") or []) if str(e).strip()]
    if absent := [e for e in elements if e not in prompt]:
        joined = "、".join(absent)
        problems.append(
            f"元素锁定清单里的这些项没有写进提示词：{joined}（四格只是机位不同，元素必须完全一致）"
        )

    # 档案上的空间事实：主轴三段、每一条锚点的名称与描述、这一次的光照状态。
    # 四视图是**空间基准图**，它存在的唯一理由就是让后续每一镜有同一个空间可依，
    # 所以这里要求得比单镜严：三段主轴齐全、每条锚点描述都落地。
    problems += _axis_problems(prompt, scene, AXIS_FIELDS, basis_required=True)
    problems += _anchor_problems(prompt, scene)
    problems += _lighting_problems(prompt, context)
    return problems


def check_shot_size(declared: str, storyboard_size: str) -> str | None:
    """成品词的景别与分镜表对不对得上。对得上返回 None。"""
    declared = (declared or "").strip()
    storyboard_size = (storyboard_size or "").strip()
    if not storyboard_size:
        return None
    if storyboard_size in DETAIL_SHOT_SIZES:
        # 局部说明直接替换"特写"二字：分镜写「特写」，成品词写「手部特写」。
        # 反过来写成「特写，手部特写」是原文点名的禁止项。
        if declared == storyboard_size or declared.endswith(storyboard_size):
            return None
        return f"景别与分镜表不一致：分镜是「{storyboard_size}」，提示词写成了「{declared}」"
    if declared != storyboard_size:
        return f"景别与分镜表不一致：分镜是「{storyboard_size}」，提示词写成了「{declared}」"
    return None


def _check_shot_image(output: Mapping[str, Any], context: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    prompt = str(output.get("prompt", ""))
    shot = dict(context.get("shot") or {})

    declared = str(output.get("shot_size", "")).strip()
    if issue := check_shot_size(declared, str(shot.get("shot_size", ""))):
        problems.append(issue)
    if declared and declared not in prompt:
        problems.append(f"景别「{declared}」没有写进提示词")

    angle = str(shot.get("angle", "") or "").strip()
    if angle and angle not in prompt:
        problems.append(f"分镜表上的拍摄角度「{angle}」没有写进提示词——用户改过的角度不能丢")

    for field, label in (
        ("facing", "角色朝向"),
        ("behind", "身后背景"),
        ("pose_lighting", "姿态表情与光影"),
    ):
        value = str(output.get(field, "")).strip()
        if not value:
            problems.append(f"五要素缺「{label}」")
        elif value not in prompt:
            problems.append(f"五要素的「{label}」没有写进提示词，自检字段与正文对不上")

    # 五要素④「身后背景」要求从空间锚点推演，而"推演过没有"只能拿 context 去比：
    # `behind` 是模型自己写的字段，它填一句"身后是走廊"同样非空、同样出现在正文里，
    # 自检字段与正文自洽，而这一镜与这个场景已经没有任何关系了。
    #
    # 主轴三段与每一条锚点事实**全部要在**，与四视图同一个标准：它们合起来是
    # 这一镜的空间参照系，不是"画面里要出现的东西清单"。远景末端同理——写全它
    # 是为了让模型知道这条轴指向哪里，至于本镜拍到哪一段，由分镜表的景别与角度
    # 决定（那两项另有校验），不由这里放松要求来决定。
    scene = dict(context.get("scene") or {})
    anchor = dict(context.get("anchor_card") or {})
    spatial = anchor if (anchor.get("camera_axis") or anchor.get("fixed_references")) else scene
    problems += _axis_problems(prompt, spatial, AXIS_FIELDS, basis_required=False)
    problems += _anchor_problems(prompt, spatial)
    problems += _lighting_problems(prompt, context)

    if "定格" in prompt:
        problems.append("镜头描述里出现了「定格」（原模板点名禁止）")
    for banned in ("字幕", "背景音乐"):
        if banned in prompt:
            problems.append(f"首帧提示词里出现了「{banned}」")
    return problems


def _check_shot_video(output: Mapping[str, Any], context: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    prompt = str(output.get("prompt", ""))
    shot = dict(context.get("shot") or {})
    cuts = [str(c).strip() for c in (output.get("cuts") or []) if str(c).strip()]

    if not prompt.rstrip().endswith(VIDEO_TAIL):
        problems.append(f"视频提示词的最后一行必须是「{VIDEO_TAIL}」，不可省略也不可移到别处")

    asset_line = str(output.get("asset_line", "")).strip()
    if asset_line and asset_line not in prompt:
        problems.append("资产标注句没有出现在提示词正文里")

    for cut in cuts:
        if not cut.startswith("【切镜】"):
            problems.append(f"切镜不是以「【切镜】」开头：{cut[:30]}")
        if cut.count("【切镜】") > 1:
            problems.append(f"两个切镜挤在同一行：{cut[:40]}")
        if cut not in prompt:
            problems.append(f"切镜没有出现在提示词正文里：{cut[:30]}")

    dialogue = str(shot.get("dialogue", "") or "").strip()
    minimum = 3 if dialogue else 2
    if len(cuts) < minimum:
        scope = "有台词的" if dialogue else ""
        problems.append(f"切镜数量不足：{scope}镜号最少 {minimum} 个切镜，实际 {len(cuts)} 个")
    if dialogue and dialogue not in prompt:
        problems.append(f"分镜表上的台词「{dialogue}」没有逐字出现在提示词里")

    sfx = str(shot.get("sfx", "") or "").strip()
    if sfx and sfx not in prompt:
        problems.append(f"分镜表上的音效「{sfx}」没有写进提示词")

    camera_move = str(shot.get("camera_move", "") or "").strip()
    if camera_move and camera_move not in prompt:
        problems.append(f"分镜表上的运镜「{camera_move}」没有写进提示词——用户改过的运镜不能丢")

    # 「背景音乐」「字幕」只允许出现在最后那行强制声明里，出现第二次就是
    # 真的在描述它们了（原模板禁止行为清单）。
    for banned in ("背景音乐", "字幕"):
        if prompt.count(banned) > 1:
            problems.append(f"提示词正文里描述了「{banned}」（只允许出现在最后一行的禁止声明中）")
    return problems


_CHECKS = {
    KIND_CHARACTER: _check_character,
    KIND_SCENE: _check_scene,
    KIND_SHOT_IMAGE: _check_shot_image,
    KIND_SHOT_VIDEO: _check_shot_video,
}


def check_output(kind: str, output: Mapping[str, Any], context: Mapping[str, Any]) -> list[str]:
    """模板级校验。返回中文问题列表，空列表表示合格。

    风格词不在这里查——它由 `check_style` 单独负责，因为那一条的失败码
    与"模板没写对"不同（前者是画风漂移，后者是结构不合格），用户看到的
    文案和该做的动作都不一样。
    """
    check = _CHECKS.get(kind)
    if check is None:
        raise ValueError(f"未知的提示词类型 {kind!r}")
    return check(output, context)
