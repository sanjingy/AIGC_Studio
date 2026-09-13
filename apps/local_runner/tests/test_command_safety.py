"""命令行构造与子环境的安全约束。

这些用例全部对着**具体的攻击面**写，不是形式主义：

* 提示词是用户内容（小说原文、剧本），里面出现 `&`、`|`、`%PATH%`、
  反引号是常态。它必须始终待在 stdin 或某个 argv 元素里，永远不变成命令。
* 子环境里留一把 API Key，一次订阅额度耗尽就静默转按量付费。
* 桥接令牌进了子环境，等于把服务端的钥匙交给一个第三方进程。
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from apps.local_runner import adapters, executor
from apps.local_runner.appserver import build_app_server_argv
from apps.local_runner.resolve import Executable

_CODEX = r"C:\fake\codex.exe"
_CLAUDE = Executable("claude", "native", r"C:\fake\claude.exe", [r"C:\fake\claude.exe"])

INJECTION = (
    "前情提要 & whoami | del /f /q C:\\ ; rm -rf / `id` $(id) %USERPROFILE% "
    "\"引号\" '单引号' \n换行\n--dangerously-skip-permissions"
)


class CommandInjectionTest(unittest.TestCase):
    def test_codex_prompt_never_reaches_argv(self) -> None:
        """Codex 侧的提示词走 JSON-RPC 消息体，命令行里只有 app-server 那几个词。

        这条在改走 app-server 之后**更强**了：以前提示词经过 stdin，
        现在它连管道上的原始字节都不是，而是一个 JSON 字段。
        """
        argv = build_app_server_argv(_CODEX)
        self.assertEqual(argv, [_CODEX, "app-server", "--listen", "stdio://"])
        for arg in argv:
            self.assertNotIn("whoami", arg)
            self.assertNotIn("rm -rf", arg)

    def test_image_prompt_keeps_the_description_verbatim(self) -> None:
        """画面描述由服务端合成（风格锁定在里面），连接器一个字都不许改。

        只在外面包一句"请调用你的图像生成工具"——协议层没有生图方法，
        不请求它模型就不会画。
        """
        described = "水墨风格，逆光，" + INJECTION
        wrapped = executor.build_image_prompt(
            prompt=described, negative_prompt="文字水印", size="1024*1024"
        )
        self.assertIn(described, wrapped)
        self.assertIn("1024×1024", wrapped)
        self.assertIn("文字水印", wrapped)
        self.assertIn("不要改写", wrapped)

    def test_claude_user_prompt_never_reaches_argv(self) -> None:
        job = adapters.build_claude(
            _CLAUDE, workdir=r"C:\tmp\w", system="SYS", user=INJECTION, model=None
        )
        self.assertEqual(job.stdin_text, INJECTION)
        for arg in job.argv:
            self.assertNotIn("whoami", arg)

    def test_long_system_prompt_falls_back_to_stdin(self) -> None:
        """超长 system 不能塞进 argv：Windows 命令行上限是 32767 个字符，
        超了进程根本起不来，而那种失败没有任何可读的报错。"""
        long_system = "锁" * 30_000
        job = adapters.build_claude(
            _CLAUDE, workdir=r"C:\tmp\w", system=long_system, user="用户", model=None
        )
        self.assertNotIn("--system-prompt", job.argv)
        self.assertIn(long_system, job.stdin_text)
        self.assertIn("用户", job.stdin_text)


class ToolRestrictionTest(unittest.TestCase):
    def test_codex_opens_no_listening_socket(self) -> None:
        """只有 stdio。`--listen ws://…` 会把一个能操作本机的 RPC 端口
        暴露出去，而我们是父子进程关系，根本不需要监听面。"""
        joined = " ".join(build_app_server_argv(_CODEX))
        self.assertIn("--listen stdio://", joined)
        self.assertNotIn("ws://", joined)
        self.assertNotIn("unix://", joined)
        self.assertNotIn("--dangerously", joined)

    def test_claude_disables_tools_mcp_and_settings(self) -> None:
        job = adapters.build_claude(_CLAUDE, workdir=r"C:\tmp\w", system="S", user="U", model=None)
        self.assertEqual(job.argv[job.argv.index("--tools") + 1], "")
        self.assertEqual(job.argv[job.argv.index("--setting-sources") + 1], "")
        self.assertEqual(job.argv[job.argv.index("--mcp-config") + 1], '{"mcpServers":{}}')
        self.assertIn("--strict-mcp-config", job.argv)
        self.assertIn("--disable-slash-commands", job.argv)
        self.assertIn("--no-session-persistence", job.argv)
        self.assertEqual(job.argv[job.argv.index("--output-format") + 1], "json")

    def test_claude_does_not_use_bare_or_permission_bypass(self) -> None:
        """`--bare` 会把认证限死成 API Key，正好关掉订阅登录——
        与试点的前提相反。绕权限的开关同样一个都不许出现。"""
        job = adapters.build_claude(_CLAUDE, workdir=r"C:\tmp\w", system="S", user="U", model=None)
        joined = " ".join(job.argv)
        self.assertNotIn("--bare", joined)
        self.assertNotIn("dangerously", joined)
        self.assertNotIn("bypassPermissions", joined)

    def test_model_is_not_hardcoded(self) -> None:
        """不传 --model 时命令行里不许出现任何模型 id。"""
        job = adapters.build_claude(_CLAUDE, workdir=r"C:\tmp\w", system="S", user="U", model=None)
        self.assertNotIn("--model", job.argv)
        job2 = adapters.build_claude(
            _CLAUDE, workdir=r"C:\tmp\w", system="S", user="U", model="opus"
        )
        self.assertEqual(job2.argv[job2.argv.index("--model") + 1], "opus")


class ChildEnvTest(unittest.TestCase):
    def test_api_keys_and_bridge_token_are_stripped(self) -> None:
        fake = {
            "PATH": r"C:\Windows",
            "USERPROFILE": r"C:\Users\x",
            "CODEX_HOME": r"C:\Users\x\.codex",
            "CLAUDE_CONFIG_DIR": r"C:\Users\x\.claude",
            "CLAUDE_CODE_OAUTH_TOKEN": "subscription-oauth",
            "ANTHROPIC_API_KEY": "sk-ant-should-not-leak",
            "ANTHROPIC_AUTH_TOKEN": "should-not-leak",
            "ANTHROPIC_BASE_URL": "https://evil.example",
            "OPENAI_API_KEY": "sk-should-not-leak",
            "AWS_SECRET_ACCESS_KEY": "should-not-leak",
            "GOOGLE_APPLICATION_CREDENTIALS": r"C:\creds.json",
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "AIGC_LOCAL_RUNNER_TOKEN": "bridge-token-should-not-leak",
            "DEEPSEEK_API_KEY": "should-not-leak",
        }
        with mock.patch.dict(os.environ, fake, clear=True):
            env = adapters.child_env()

        for removed in (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "OPENAI_API_KEY",
            "AWS_SECRET_ACCESS_KEY",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "CLAUDE_CODE_USE_BEDROCK",
            "AIGC_LOCAL_RUNNER_TOKEN",
            "DEEPSEEK_API_KEY",
        ):
            self.assertNotIn(removed, env, f"{removed} 泄进了子进程环境")
        self.assertNotIn("should-not-leak", "\n".join(env.values()))

        # 订阅登录要保住，不能一刀切。
        for kept in ("PATH", "USERPROFILE", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
            self.assertIn(kept, env)
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "subscription-oauth")


if __name__ == "__main__":
    unittest.main()
