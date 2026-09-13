"""Claude headless 的命令行与输出解析，以及两边共用的子环境净化。

**这里只剩 claude 了。** Codex 那条 `codex exec` 一次性子进程的路径已经
删掉：同一个 CLI 没有理由维护两套执行路径，而官方 `codex app-server`
那条（`executor.py` + `appserver.py` + `imagegen.py`）还多给了打断、
原生生图产物和账号事件。删掉的是**重复的管理机制**，不是能力——
Codex 的文本照样跑，只是改走 app-server。

Gemini CLI 本机没装（`gemini` 不在 PATH 上，2026-09-07 核实过），
所以这里**没有** gemini 适配器，也不打算写一个没法验证的。见
`project_docs/modules/15_LOCAL_RUNTIME.md`「已推迟」。

参数全部来自 2026-09-07 在本机跑 `--help` 的实际输出（claude 2.1.263），
不是从文档抄的，更没有臆造。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from apps.local_runner.process import Outcome
from apps.local_runner.resolve import Executable

#: 子进程环境里按前缀剔除的变量。**目的只有一个：不让本地这次调用
#: 变成按量付费。** CLI 在订阅额度耗尽时会静默切到 API Key，那是真金白银，
#: 而用户以为自己在用订阅。
#:
#: **但这一层挡不住配置文件。** 2026-09-07 在本机复现过：
#: `~/.claude/settings.json` 里有 `env: {ANTHROPIC_AUTH_TOKEN, ANTHROPIC_BASE_URL}`，
#: 那是 CLI **自己**读了自己的设置再注入的，父进程的环境干干净净也没用。
#: 所以这条防线有两个同伴，缺一不可：
#:
#: 1. `build_claude` 的 `--setting-sources ""`（不加载任何设置源）；
#: 2. `executor.probe_claude_account()` —— 真查一次官方的
#:    `claude auth status --json`，不是"我们清了环境变量所以应该没问题"。
_DENY_PREFIXES = (
    "ANTHROPIC_",  # API_KEY / AUTH_TOKEN / BASE_URL / MODEL …
    "OPENAI_",
    "AZURE_OPENAI_",
    "AWS_",  # Bedrock
    "GOOGLE_",  # Vertex
    "GEMINI_",
    "VERTEX_",
    "CLAUDE_CODE_USE_",  # USE_BEDROCK / USE_VERTEX / USE_FOUNDRY
    "AIGC_LOCAL_RUNNER_",  # 桥接令牌绝不进子进程
)

#: 逐个点名剔除的。
_DENY_EXACT = frozenset(
    {
        "CODEX_API_KEY",
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "DASHSCOPE_API_KEY",
        "ARK_API_KEY",
    }
)

#: **保留**的（这里写出来是为了防止后人一刀切）：`CODEX_HOME`、
#: `CLAUDE_CONFIG_DIR`、`CLAUDE_CODE_OAUTH_TOKEN`、`PATH`、`USERPROFILE`、
#: `APPDATA` / `LOCALAPPDATA`。前三个是订阅登录的落点，删掉等于让 CLI
#: 变成未登录状态；后面几个是 Windows 上进程能起来的最低要求。

#: 提示词作为 argv 传递的长度预算。Windows 的 CreateProcess 命令行上限是
#: 32767 个字符，整条命令行共用这个额度。超了就退回 stdin 拼接。
_ARGV_PROMPT_BUDGET = 20_000

_AUTH_PATTERNS = re.compile(
    r"not logged in|please log ?in|/login|unauthorized|401|invalid api key"
    r"|authentication (failed|required)|credentials",
    re.IGNORECASE,
)
_RATE_PATTERNS = re.compile(
    r"rate limit|usage limit|quota|429|too many requests|overloaded|capacity",
    re.IGNORECASE,
)


@dataclass
class Job:
    argv: list[str]
    stdin_text: str
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Parsed:
    """解析结果。`error_code` 非空即失败，`text` 只在成功时有值。

    **`error_code` 是有界枚举里的值。** 子进程的 stderr 只在本地用于分类，
    一个字节都不回传服务端：那里面有本机路径、用户名，以及登录提示里的账号。
    """

    text: str | None
    error_code: str | None
    model_id: str | None
    tokens_in: int | None
    tokens_out: int | None


def child_env() -> dict[str, str]:
    """给子进程的环境。父环境去掉凭据类变量，其余原样保留。

    用黑名单而不是白名单：CLI 需要的环境变量在不同 Windows 安装上并不
    一致（`SystemRoot`、`ComSpec`、`PATHEXT`、代理设置…），白名单漏一个
    就是"在我机器上能跑"。要防的东西是可枚举的，就按可枚举的那面写。
    """
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper in _DENY_EXACT:
            continue
        if any(upper.startswith(prefix) for prefix in _DENY_PREFIXES):
            continue
        env[key] = value
    return env


def _merge_prompt(system: str, user: str) -> str:
    return f"{system.rstrip()}\n\n---\n\n{user}"


def build_claude(
    exe: Executable, *, workdir: str, system: str, user: str, model: str | None
) -> Job:
    """`claude -p`。

    每个开关的理由：

    * `-p --output-format json` 是官方 headless 用法，输出里带 `result`。
    * `--tools ""` 关掉全部内置工具。这一步只需要它写文本。
    * `--strict-mcp-config --mcp-config '{"mcpServers":{}}'` 忽略一切
      其他 MCP 配置，且显式给一个空的。
    * `--setting-sources ""` 不加载 user / project / local 设置，
      于是 hooks、插件、自定义 agent 都不生效。**这一条不只是"干净"，
      它是本条路径不变成按量付费的关键**：用户的 `~/.claude/settings.json`
      里可以有 `env: {ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN}`，
      那是 CLI 自己读自己的设置再注入的，`child_env()` 清父环境挡不住。
      2026-09-07 本机实测（`claude doctor`，免费、不发模型请求）：
      不带这个开关时报 "ANTHROPIC_BASE_URL is set and does not point at
      api.anthropic.com" 且 "claude.ai subscription auth not active"；
      带上 `--setting-sources ""` 之后两句话都消失，转为
      "Organization policy: not applicable to Pro and Max accounts"，
      即回到订阅通道。
    * `--disable-slash-commands` 关掉 skill。
    * `--permission-prompts none` 任何会弹权限的动作直接拒绝——
      非交互下没人能回答，弹了就是挂死。
    * `--no-session-persistence` 不落会话文件。
    * **不用 `--bare`**：它把 Anthropic 认证限死成 `ANTHROPIC_API_KEY`
      或 apiKeyHelper，OAuth 与 keychain 一概不读，正好把订阅登录关掉，
      与试点的前提相反。
    * **不用** `--dangerously-skip-permissions` / `--permission-mode
      bypassPermissions`：工具都关了，用不上，而它们是真正危险的开关。
    """
    argv = [
        *exe.argv_prefix,
        "-p",
        "--output-format",
        "json",
        "--tools",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--setting-sources",
        "",
        "--disable-slash-commands",
        "--permission-prompts",
        "none",
        "--no-session-persistence",
    ]
    if model:
        argv += ["--model", model]

    if len(system) <= _ARGV_PROMPT_BUDGET:
        # 走真正的 system 位。这比拼进用户消息好：默认的编码助手系统提示词
        # 会被替换掉，模型不会拿"我是个写代码的"这层身份去理解任务。
        argv += ["--system-prompt", system]
        stdin_text = user
    else:
        # 超长时退回拼接。命令行超过 Windows 的 32767 上限会直接起不来，
        # 那种失败比"系统提示词降级成用户消息的一段"糟得多。
        stdin_text = _merge_prompt(system, user)

    return Job(argv=argv, stdin_text=stdin_text, env=child_env())


def classify(*fragments: str) -> str:
    """把本地看到的失败归到有界枚举里。**只看，不外传。**"""
    blob = "\n".join(f for f in fragments if f)
    if _AUTH_PATTERNS.search(blob):
        return "auth_required"
    if _RATE_PATTERNS.search(blob):
        return "rate_limited"
    return "cli_error"


def parse_claude(job: Job, outcome: Outcome, *, model: str | None) -> Parsed:
    del job
    if outcome.timed_out:
        return Parsed(None, "timeout", None, None, None)

    raw = outcome.stdout.strip()
    start, end = raw.find("{"), raw.rfind("}")
    if outcome.returncode != 0 and (start == -1 or end <= start):
        return Parsed(None, classify(outcome.stderr, raw), None, None, None)
    if start == -1 or end <= start:
        return Parsed(None, "cli_error", None, None, None)

    try:
        data = json.loads(raw[start : end + 1])
    except ValueError:
        return Parsed(None, "cli_error", None, None, None)
    if not isinstance(data, dict):
        return Parsed(None, "cli_error", None, None, None)

    result = data.get("result")
    if data.get("is_error") or data.get("subtype") not in (None, "success"):
        return Parsed(
            None,
            classify(outcome.stderr, result if isinstance(result, str) else ""),
            None,
            None,
            None,
        )
    if not isinstance(result, str) or not result.strip():
        return Parsed(None, "cli_error", None, None, None)

    usage = data.get("usage")
    tokens_in = tokens_out = None
    if isinstance(usage, dict):
        # 有就用，没有就 None。字段缺失时不推算。
        raw_in, raw_out = usage.get("input_tokens"), usage.get("output_tokens")
        tokens_in = raw_in if isinstance(raw_in, int) else None
        tokens_out = raw_out if isinstance(raw_out, int) else None

    reported_model = data.get("model")
    model_id = reported_model if isinstance(reported_model, str) and reported_model else None
    return Parsed(
        result.strip(),
        None,
        model_id or model or "local-cli.claude",
        tokens_in,
        tokens_out,
    )
