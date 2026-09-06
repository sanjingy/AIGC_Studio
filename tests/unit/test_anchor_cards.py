"""空间锚点卡的判据（ADR-037 第 3 条）。

这些用例守的是 `apps/api/modules/agent/anchors.py` 顶部那处**顺序矛盾的解法**：
ADR 给的三条判据（≥3 个镜号 / ≥2 人同框 / 有明显位移）的输入全长在分镜表上，
而门③ 在分镜**之前**。解法是把输入换成剧本，判据本身一条不少：

    ≥3 个镜号  ← 该地点下的节拍总数 ≥ 3（节拍数是镜号数的下界）
    ≥2 人同场  ← 单场戏里同时出现的角色数 ≥ 2
    明显位移    ← 该地点跨 ≥2 场戏，或单个地点里的动作节拍 ≥ 2

下界替换的正确性靠"宁可多出一张卡"来兜底，所以这里的用例要同时钉住两件事：
判据在该命中时命中（不漏），以及**误差只往多出卡的方向倒**（漏判才是灾难，
多判只是让用户在门③ 上多看两行）。

零 Provider 调用、零数据库：全是纯函数。
"""

from __future__ import annotations

from typing import Any

from apps.api.modules.agent import anchors


def _scene(location: str, beats: list[dict[str, Any]], refs: list[str] | None = None) -> dict:
    return {
        "id": f"1-{location}",
        "location": location,
        "character_refs": refs or [],
        "beats": beats,
    }


def _action(text: str = "推门进来") -> dict[str, Any]:
    return {"kind": "action", "text": text}


def _line(ref: str, text: str = "你来了") -> dict[str, Any]:
    return {"kind": "dialogue", "character_ref": ref, "text": text}


def _state(
    script_scenes: list[dict[str, Any]],
    sheets: list[dict[str, Any]],
    storyboard: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "screenplay": {"episodes": [{"index": 1, "scenes": script_scenes}]},
        "scenes": {"scenes": sheets},
    }
    if storyboard is not None:
        state["storyboard"] = storyboard
    return state


def _sheet(ref: str, name: str, *, anchored: bool = True) -> dict[str, Any]:
    sheet: dict[str, Any] = {"ref": ref, "name": name}
    if anchored:
        sheet["camera_axis"] = {"position": "门外路面", "facing": "朝向建筑正面"}
        sheet["fixed_references"] = [
            {"name": "锈迹铁门", "description": "铁门在正前方，右扇下缘锈穿一个洞"},
            {"name": "青石台阶", "description": "石阶在右侧，第三级缺一角"},
        ]
    return sheet


# ------------------------------------------------------------------ 判据命中


def test_three_beats_earn_a_card() -> None:
    """节拍数 ≥ 3 就出卡——它是镜号数的下界。

    下界的论证：分镜提示词要求逐节点拆镜，动作和台词各占一条，所以一个
    节拍至少拆一个镜号。用同一个阈值卡下界只会多判，不会漏判。
    """
    state = _state(
        [_scene("资料馆门口", [_action(), _action("回头"), _action("推门")])],
        [_sheet("gate", "资料馆门口")],
    )
    card = anchors.anchor_cards(state)[0]
    assert card.needs_card
    assert card.signals.beats == 3
    assert any("节拍" in r for r in card.reasons)


def test_two_people_in_one_scene_earn_a_card() -> None:
    """同框 2 人就出卡：两个人的相对位置一旦这镜左下镜右，观众立刻出戏。"""
    state = _state(
        [_scene("馆长室", [_line("zhu_jue")], refs=["guan_zhang"])],
        [_sheet("office", "馆长室")],
    )
    card = anchors.anchor_cards(state)[0]
    assert card.needs_card
    assert card.signals.max_cast == 2
    assert any("同时在场" in r for r in card.reasons)


def test_cast_is_the_union_of_the_roster_and_the_speaking_beats() -> None:
    """名单和台词节拍取并集：编剧填的 `character_refs` 会漏人。

    模型经常只填主要角色，而台词节拍里明明还有第三个人在说话。
    漏算的后果是该出卡的场景没出卡。
    """
    state = _state(
        [_scene("走廊", [_line("a"), _line("b")], refs=["c"])],
        [_sheet("hall", "走廊")],
    )
    assert anchors.anchor_cards(state)[0].signals.max_cast == 3


def test_cast_is_per_scene_max_not_a_running_total() -> None:
    """同框人数取单场最大值，不跨场累加。

    两场戏各来一个人不构成"同时在场"。累加会把一个从头到尾只有独角戏的
    地点判成需要锚点卡。
    """
    state = _state(
        [
            _scene("巷口", [_line("a")], refs=["a"]),
            _scene("巷口", [_line("b")], refs=["b"]),
        ],
        [_sheet("alley", "巷口")],
    )
    card = anchors.anchor_cards(state)[0]
    assert card.signals.max_cast == 1
    # 但它跨了两场戏，位移判据仍然命中
    assert card.needs_card
    assert any("位移" in r for r in card.reasons)


def test_two_action_beats_count_as_movement() -> None:
    state = _state(
        [_scene("天台", [_action("走到栏杆"), _action("转身")])],
        [_sheet("roof", "天台")],
    )
    card = anchors.anchor_cards(state)[0]
    assert card.signals.action_beats == 2
    assert any("位移" in r for r in card.reasons)


# ------------------------------------------------------------------ 判据不命中


def test_a_quiet_one_beat_location_needs_no_card() -> None:
    """一场戏、一个节拍、一个人的地点用内联描述就够了。

    给它出卡不是保险起见，是往门③ 上堆噪声——ADR-037 要求一屏看完，
    每多一张不必要的卡都在挤掉真正需要看的那几张。
    """
    state = _state(
        [_scene("电话亭", [_line("a")], refs=["a"])],
        [_sheet("booth", "电话亭")],
    )
    card = anchors.anchor_cards(state)[0]
    assert not card.needs_card
    assert card.reasons == []


def test_a_scene_sheet_with_no_matching_location_gets_no_card() -> None:
    """场景档案的 name 与剧本 location 对不上时按空信号处理。

    判不出来就不出卡，而不是凭空造一张没有依据的卡：卡上的理由要能
    指回剧本，指不回去的理由用户没法核对。这也是 `visual_scene.yaml`
    里"name 必须与剧本 location 完全一致"那条要求的另一半。
    """
    state = _state(
        [_scene("资料馆门口", [_action(), _action(), _action()])],
        [_sheet("gate", "馆门口")],  # 少了两个字
    )
    card = anchors.anchor_cards(state)[0]
    assert not card.needs_card
    assert card.signals == anchors.AnchorSignals()


# ------------------------------------------------------------------ 空卡预警


def test_a_card_without_anchors_is_flagged_incomplete() -> None:
    """出了卡但 `camera_axis` / `fixed_references` 是空的，要能指出来。

    空卡和没有卡对下游是一回事，但界面上它看起来是"已确认"。
    这两个字段在 schema 上一直存在、一直没人填，正是这次要补的洞。
    """
    state = _state(
        [_scene("仓库", [_action(), _action(), _action()])],
        [_sheet("depot", "仓库", anchored=False)],
    )
    card = anchors.anchor_cards(state)[0]
    assert card.needs_card
    assert card.incomplete

    payload = anchors.gate_payload(state)
    assert payload["incomplete_refs"] == ["depot"]


def test_a_complete_card_is_not_flagged() -> None:
    state = _state(
        [_scene("仓库", [_action(), _action(), _action()])],
        [_sheet("depot", "仓库")],
    )
    assert not anchors.anchor_cards(state)[0].incomplete


# ------------------------------------------------------------------ 门③ 的摘要形状


def test_gate_payload_lists_every_scene_exactly_once() -> None:
    """出卡的 + 不出卡的 = 全部场景。

    ADR-037 禁止的是逐个确认，不是不给用户看全景：只列出卡的那几个，
    用户答不了"是不是漏了哪个场景"。
    """
    state = _state(
        [
            _scene("资料馆门口", [_action(), _action(), _action()]),
            _scene("电话亭", [_line("a")], refs=["a"]),
        ],
        [_sheet("gate", "资料馆门口"), _sheet("booth", "电话亭")],
    )
    payload = anchors.gate_payload(state)

    assert payload["scenes_total"] == 2
    assert [c["ref"] for c in payload["cards"]] == ["gate"]
    assert [c["ref"] for c in payload["inline"]] == ["booth"]
    # 判据是剧本推的下界，摘要要如实说明来源，否则用户会当成最终镜号数
    assert payload["criteria_source"] == "screenplay"


def test_gate_payload_keeps_the_scene_sheet_order() -> None:
    """不排序：场景档案的顺序就是剧本里地点的出现顺序。

    按 ref 字母序排会让用户在门③ 上看到的排列和他读剧本的顺序对不上。
    """
    sheets = [_sheet("zoo", "动物园"), _sheet("attic", "阁楼"), _sheet("mall", "商场")]
    script = [
        _scene(name, [_action(), _action(), _action()]) for name in ("动物园", "阁楼", "商场")
    ]
    payload = anchors.gate_payload(_state(script, sheets))
    assert [c["ref"] for c in payload["cards"]] == ["zoo", "attic", "mall"]


def test_gate_payload_on_an_empty_project_is_empty_not_broken() -> None:
    """还没跑场景档案时门③ 不该炸。摘要是给界面渲染的，炸了就是白屏。"""
    payload = anchors.gate_payload({})
    assert payload["scenes_total"] == 0
    assert payload["cards"] == []
    assert payload["inline"] == []


# ------------------------------------------------------------------ 分镜之后的残差补偿


def test_reconcile_finds_a_scene_the_script_underestimated() -> None:
    """剧本估不足、分镜才够格的场景，要在门④ 上列出来。

    这是下界估计的残差：一个节拍被拆成了三个镜号。它不是第五道门，
    只是门④ 摘要里的一行提示。
    """
    state = _state(
        [_scene("地下室", [_action()])],  # 剧本上只有一个节拍 → 门③ 没出卡
        [_sheet("cellar", "地下室")],
        storyboard={
            "shots": [{"index": i, "scene_ref": "cellar", "camera_move": "推"} for i in range(1, 4)]
        },
    )
    assert not anchors.anchor_cards(state)[0].needs_card

    gaps = anchors.reconcile_after_storyboard(state)
    assert [g["ref"] for g in gaps] == ["cellar"]
    assert any("实际拆出 3 个镜号" in r for r in gaps[0]["reasons"])


def test_reconcile_skips_scenes_that_already_have_a_card() -> None:
    """门③ 上已经出过卡的场景不再重复提示——用户已经确认过它了。"""
    state = _state(
        [_scene("地下室", [_action(), _action(), _action()])],
        [_sheet("cellar", "地下室")],
        storyboard={"shots": [{"index": i, "scene_ref": "cellar"} for i in range(1, 5)]},
    )
    assert anchors.anchor_cards(state)[0].needs_card
    assert anchors.reconcile_after_storyboard(state) == []


def test_reconcile_returns_nothing_before_the_storyboard_exists() -> None:
    """分镜还没跑就没有"真判据"可用。空列表，不是猜。"""
    state = _state([_scene("地下室", [_action()])], [_sheet("cellar", "地下室")])
    assert anchors.reconcile_after_storyboard(state) == []


def test_reconcile_ignores_shots_pointing_at_an_unknown_scene() -> None:
    """分镜引用了一个场景档案里没有的 ref 时不要凭空造一行提示。

    那是分镜表自己的问题（`visual.storyboard.v1` 被要求只能用已有的
    场景 ref），在门④ 上报一个"这个场景锚点可能不够"只会误导。
    """
    state = _state(
        [_scene("地下室", [_action()])],
        [_sheet("cellar", "地下室")],
        storyboard={"shots": [{"index": i, "scene_ref": "nowhere"} for i in range(1, 5)]},
    )
    assert anchors.reconcile_after_storyboard(state) == []


# ------------------------------------------------------------------ 阈值来自 ADR


def test_thresholds_match_the_adr() -> None:
    """判据阈值是产品判据（ADR-037 第 3 条正文），不是运营参数。

    它写成常量而不是入库，是因为改它等于改这条 ADR。这条断言的作用是
    让"有人顺手把 3 改成 5"这件事在评审之外也会被发现一次。
    """
    assert anchors.MIN_SHOTS_FOR_CARD == 3
    assert anchors.MIN_CAST_FOR_CARD == 2
    assert anchors.MIN_ACTION_BEATS_FOR_MOVEMENT == 2
    assert anchors.MIN_SCRIPT_SCENES_FOR_MOVEMENT == 2
