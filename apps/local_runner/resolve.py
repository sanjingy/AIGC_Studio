"""找出真正该执行的可执行文件。

Windows 上 `codex` / `claude` 在 PATH 里通常是 npm 的 **shim**
（`codex.cmd` / `codex.ps1`）。直接跑 `.cmd` 意味着走 `cmd.exe`，
而 cmd 的转义规则与 Python 的 `subprocess` 列表参数**不是一回事**——
参数里的 `&`、`^`、`%VAR%` 会被 cmd 再解释一遍。提示词是用户内容，
里面出现这些字符是常态，所以这条路必须堵死。

解析优先级：

1. 环境变量显式指定（`AIGC_LOCAL_RUNNER_CODEX_BIN` / `..._CLAUDE_BIN`）
2. PATH 上的**原生 `.exe`**（跳过 `.cmd` / `.bat` / `.ps1`）
3. shim 同目录下 npm 包里的原生二进制
4. `node <包内 js 入口>` —— 已安装的可信入口，仍然不经过任何 shell

四条都找不到就是没装，如实报告，不猜。
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

#: 绝不执行的扩展名：它们都要经过一层 shell 解释。
_SHELL_SUFFIXES = {".cmd", ".bat", ".ps1", ".sh"}

#: 每个 provider 的 npm 包名与包内候选路径。
#: 通配符只用在 codex 的 vendor 目录上——那一段带 CPU 架构，写死会在
#: arm64 机器上直接失效。
_PACKAGES: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    # provider: (包名, 原生二进制候选（相对包根，可含 *）, node 入口候选)
    "codex": (
        "@openai/codex",
        (
            "node_modules/@openai/codex-win32-*/vendor/*/bin/codex.exe",
            "node_modules/@openai/codex-*/vendor/*/bin/codex",
            "vendor/*/bin/codex.exe",
            "vendor/*/bin/codex",
        ),
        ("bin/codex.js",),
    ),
    "claude": (
        "@anthropic-ai/claude-code",
        (
            "bin/claude.exe",
            "bin/claude",
            "node_modules/@anthropic-ai/claude-code-*/claude.exe",
            "node_modules/@anthropic-ai/claude-code-*/claude",
        ),
        ("cli.js",),
    ),
}


@dataclass(frozen=True)
class Executable:
    """怎么启动这个 CLI。`argv_prefix` 已经是最终的参数列表前缀。"""

    provider: str
    #: 展示用：解析到的东西是什么（原生二进制 / node 入口）
    kind: str
    path: str
    argv_prefix: list[str]

    @property
    def display(self) -> str:
        return f"{self.kind}: {self.path}"


class NotInstalledError(Exception):
    """本机上找不到这个 CLI。**不猜、不装、不假装支持。**"""


def _env_override(provider: str) -> Path | None:
    raw = os.environ.get(f"AIGC_LOCAL_RUNNER_{provider.upper()}_BIN", "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_file():
        raise NotInstalledError(f"{provider}: 环境变量指定的可执行文件不存在：{path}")
    if path.suffix.lower() in _SHELL_SUFFIXES:
        raise NotInstalledError(
            f"{provider}: 拒绝执行 shell 包装脚本 {path.name}，请指向原生可执行文件"
        )
    return path


def _native_on_path(name: str) -> Path | None:
    """只认原生可执行文件。

    Windows 上不能用 `shutil.which`：它按 PATHEXT 依次尝试，而默认的
    PATHEXT 里 `.CMD` 排在 `.EXE` 前面，于是先命中 npm shim。
    这里只找 `.exe`，找不到就交给后面的包内解析。
    """
    if os.name == "nt":
        for entry in os.environ.get("PATH", "").split(os.pathsep):
            if not entry:
                continue
            candidate = Path(entry) / f"{name}.exe"
            if candidate.is_file():
                return candidate
        return None

    found = shutil.which(name)
    if found and Path(found).suffix.lower() not in _SHELL_SUFFIXES:
        return Path(found)
    return None


def _shim_dirs(name: str) -> list[Path]:
    """PATH 上出现过这个命令（任何形态）的目录。npm 包就装在它旁边。"""
    dirs: list[Path] = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        base = Path(entry)
        for suffix in ("", ".exe", ".cmd", ".ps1"):
            if (base / f"{name}{suffix}").is_file():
                if base not in dirs:
                    dirs.append(base)
                break
    return dirs


def _package_roots(name: str, package: str) -> list[Path]:
    roots: list[Path] = []
    for base in _shim_dirs(name):
        for candidate in (
            base / "node_modules" / package,
            base / ".." / "lib" / "node_modules" / package,
        ):
            resolved = candidate.resolve()
            if resolved.is_dir() and resolved not in roots:
                roots.append(resolved)
    return roots


def _first_match(root: Path, patterns: tuple[str, ...]) -> Path | None:
    for pattern in patterns:
        if "*" in pattern:
            matches = sorted(root.glob(pattern))
        else:
            candidate = root / pattern
            matches = [candidate] if candidate.is_file() else []
        for match in matches:
            if match.is_file() and match.suffix.lower() not in _SHELL_SUFFIXES:
                return match
    return None


def resolve(provider: str) -> Executable:
    """解析一个 provider 的启动方式。找不到抛 :class:`NotInstalledError`。"""
    if provider not in _PACKAGES:
        raise NotInstalledError(f"未知 provider：{provider}")
    package, native_patterns, node_entries = _PACKAGES[provider]

    override = _env_override(provider)
    if override is not None:
        return Executable(provider, "explicit", str(override), [str(override)])

    native = _native_on_path(provider)
    if native is not None:
        return Executable(provider, "native", str(native), [str(native)])

    roots = _package_roots(provider, package)
    for root in roots:
        match = _first_match(root, native_patterns)
        if match is not None:
            return Executable(provider, "native", str(match), [str(match)])

    node = _native_on_path("node")
    if node is not None:
        for root in roots:
            for entry in node_entries:
                script = root / entry
                if script.is_file():
                    return Executable(
                        provider,
                        "node-entrypoint",
                        str(script),
                        [str(node), str(script)],
                    )

    raise NotInstalledError(
        f"{provider}: 本机找不到原生可执行文件，也找不到 {package} 的包内入口。"
        f" 装好官方 CLI 后重试，或用 AIGC_LOCAL_RUNNER_{provider.upper()}_BIN 显式指定。"
    )
