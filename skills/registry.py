"""Skill 注册表：加载并校验内置与用户上传的 Skill。

目录约定与 Agent 一致：

    skills/builtin/*.yaml   平台内置，随代码发布
    skills/custom/*.yaml    用户或第三方提供，可热加载

两者走**同一套校验**。内置不享受特权——否则"外部 Skill 受约束"
就成了一句空话。
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

import yaml

from agents import registry as agent_registry
from skills.spec import SkillSpec, load_spec

_ROOT = pathlib.Path(__file__).resolve().parent
BUILTIN_DIR = _ROOT / "builtin"
CUSTOM_DIR = _ROOT / "custom"

MAX_SPEC_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class LoadReport:
    """加载结果。

    坏的 spec 不能让整个注册表加载失败——一个第三方 Skill 写错了，
    不该导致平台起不来。
    """

    specs: dict[str, SkillSpec]
    errors: dict[str, str]

    def for_route(self, route: str) -> list[SkillSpec]:
        return [s for s in self.specs.values() if s.route == route and s.status == "active"]


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
    specs: dict[str, SkillSpec] = {}
    errors: dict[str, str] = {}

    for source, directory in (
        ("builtin", builtin_dir or BUILTIN_DIR),
        ("custom", custom_dir or CUSTOM_DIR),
    ):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            try:
                spec = load_spec(_read_yaml(path), source=source)
                if spec.id in specs:
                    raise ValueError(f"id {spec.id!r} 与已加载的 spec 重复")
                specs[spec.id] = spec
            except Exception as exc:
                errors[path.name] = f"{type(exc).__name__}: {exc}"

    return LoadReport(specs=specs, errors=errors)


_cache: LoadReport | None = None


def registry(*, reload: bool = False) -> LoadReport:
    global _cache
    if _cache is None or reload:
        _cache = load()
    return _cache


def get(skill_id: str) -> SkillSpec | None:
    return registry().specs.get(skill_id)


def default_for(route: str) -> SkillSpec:
    """取某条路线的默认 Skill。

    优先级：自定义 > 内置（04_SkillSpec.md 的选择优先级）。
    """
    candidates = registry().for_route(route)
    if not candidates:
        raise LookupError(f"没有可用于路线 {route!r} 的 Skill")
    custom = [s for s in candidates if s.source == "custom"]
    return (custom or candidates)[0]


def missing_agents(spec: SkillSpec) -> list[str]:
    """spec 钉死引用但注册表里不存在的 Agent id。

    单独一个函数而不是塞进 schema 校验：Agent 注册表是热加载的，
    一个用户 Agent 临时加载失败不该让整条生产线的定义变成非法。
    调用方自己决定是拒绝执行还是退回按 role 取默认。
    """
    known = agent_registry.registry().specs
    return [aid for aid in spec.agent_ids() if aid not in known]
