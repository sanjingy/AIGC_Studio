"""导入冒烟测试。

存在的理由：`Redis[str]` 这种写法能通过 mypy，但运行时会抛
"is not a generic class"——类型层和运行时不一致的问题，静态检查抓不到。
每个模块至少被导入一次，这类问题就会在 CI 立刻暴露，而不是在部署后。
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

PACKAGES = ["apps", "worker", "packages"]


def _iter_modules() -> list[str]:
    found: list[str] = []
    for pkg_name in PACKAGES:
        pkg = importlib.import_module(pkg_name)
        found.append(pkg_name)
        for mod in pkgutil.walk_packages(pkg.__path__, prefix=f"{pkg_name}."):
            found.append(mod.name)
    return sorted(found)


@pytest.mark.parametrize("module_name", _iter_modules())
def test_module_imports(module_name: str) -> None:
    importlib.import_module(module_name)
