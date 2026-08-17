"""Agent 注册表：加载并校验内置与自定义 Agent。

目录约定：
    agents/builtin/*.yaml   平台内置，随代码发布
    agents/custom/*.yaml    用户或第三方提供，可热加载

两者走**同一套校验**。内置不享受特权——否则"外部 Agent 受约束"
就成了一句空话。
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

import yaml

from agents.spec import AgentSpec

_ROOT = pathlib.Path(__file__).resolve().parent
BUILTIN_DIR = _ROOT / "builtin"
CUSTOM_DIR = _ROOT / "custom"

# 单个 spec 文件的大小上限。用户上传的东西必须有边界，
# 否则一个 50MB 的 YAML 就能把加载过程拖死。
MAX_SPEC_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class LoadReport:
    """加载结果。坏的 spec 不能让整个注册表加载失败——

    一个第三方 Agent 写错了，不该导致平台起不来。
    记录下来、跳过它、继续加载其余的。
    """

    specs: dict[str, AgentSpec]
    errors: dict[str, str]

    def by_role(self, role: str) -> list[AgentSpec]:
        return [s for s in self.specs.values() if s.role == role]


def _read_yaml(path: pathlib.Path) -> dict[str, object]:
    if path.stat().st_size > MAX_SPEC_BYTES:
        raise ValueError(f"spec 文件超过 {MAX_SPEC_BYTES} 字节上限")
    # safe_load 不是可选项：yaml.load 能构造任意 Python 对象，
    # 加载用户文件时等于远程代码执行。
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("spec 顶层必须是映射")
    return data


def load(
    *, builtin_dir: pathlib.Path | None = None, custom_dir: pathlib.Path | None = None
) -> LoadReport:
    specs: dict[str, AgentSpec] = {}
    errors: dict[str, str] = {}

    # 先加载内置，自定义才能 extends 它们
    for source, directory in (
        ("builtin", builtin_dir or BUILTIN_DIR),
        ("custom", custom_dir or CUSTOM_DIR),
    ):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            try:
                raw = _read_yaml(path)
                raw["source"] = source
                spec = AgentSpec.model_validate(raw)

                if spec.extends:
                    base = specs.get(spec.extends)
                    if base is None:
                        raise ValueError(f"extends 的 {spec.extends!r} 不存在")
                    spec = spec.merged_with(base)

                if spec.id in specs:
                    raise ValueError(f"id {spec.id!r} 与已加载的 spec 重复")

                specs[spec.id] = spec
            except Exception as exc:
                # 单个 spec 出错不影响其余——第三方写错不该拖垮平台
                errors[path.name] = f"{type(exc).__name__}: {exc}"

    return LoadReport(specs=specs, errors=errors)


_cache: LoadReport | None = None


def registry(*, reload: bool = False) -> LoadReport:
    global _cache
    if _cache is None or reload:
        _cache = load()
    return _cache


def get(agent_id: str) -> AgentSpec | None:
    return registry().specs.get(agent_id)


def default_for(role: str) -> AgentSpec:
    """取某个角色的默认 Agent。

    优先级：自定义 > 内置（04_SkillSpec.md 的 Skill 选择优先级同理）。
    这样第三方 Agent 只要声明同一个 role 就能直接顶替内置的。
    """
    candidates = registry().by_role(role)
    if not candidates:
        raise LookupError(f"没有可用于角色 {role!r} 的 Agent")
    custom = [s for s in candidates if s.source == "custom"]
    return (custom or candidates)[0]
