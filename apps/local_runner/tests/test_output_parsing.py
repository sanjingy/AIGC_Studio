"""输出解析与失败分类。

关键点：**只取最终消息，不取工具事件**；失败一律收敛成有界枚举，
子进程的 stderr 一个字节都不外传。
"""

from __future__ import annotations

import json
import unittest

from apps.local_runner import adapters
from apps.local_runner.process import Outcome


def _outcome(
    *, code: int | None = 0, stdout: str = "", stderr: str = "", timed_out: bool = False
) -> Outcome:
    return Outcome(returncode=code, stdout=stdout, stderr=stderr, timed_out=timed_out)


# Codex 侧的输出解析已经不在这里了：`codex exec` 那条一次性子进程的路径
# 整条删掉，Codex 改走官方 app-server，产物是协议事件而不是 stdout 文本。
# 对应的用例在 `test_turn_flow.py`（只取最终 agentMessage、跳过工具事件）。


class ClaudeParsingTest(unittest.TestCase):
    def _job(self) -> adapters.Job:
        return adapters.Job(argv=[], stdin_text="")

    def test_success_shape(self) -> None:
        payload = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": '{"passed": true}',
            "usage": {"input_tokens": 120, "output_tokens": 34},
            "model": "claude-opus-5",
        }
        parsed = adapters.parse_claude(
            self._job(), _outcome(stdout=json.dumps(payload)), model=None
        )
        self.assertEqual(parsed.text, '{"passed": true}')
        self.assertEqual(parsed.tokens_in, 120)
        self.assertEqual(parsed.tokens_out, 34)
        self.assertEqual(parsed.model_id, "claude-opus-5")

    def test_is_error_becomes_a_bounded_code(self) -> None:
        payload = {"type": "result", "subtype": "error_during_execution", "is_error": True}
        parsed = adapters.parse_claude(
            self._job(), _outcome(code=1, stdout=json.dumps(payload)), model=None
        )
        self.assertEqual(parsed.error_code, "cli_error")
        self.assertIsNone(parsed.text)

    def test_unparseable_output_is_not_returned_as_text(self) -> None:
        parsed = adapters.parse_claude(
            self._job(), _outcome(stdout="就是一段不是 JSON 的话"), model=None
        )
        self.assertEqual(parsed.error_code, "cli_error")
        self.assertIsNone(parsed.text)

    def test_empty_result_is_a_failure_not_an_empty_success(self) -> None:
        payload = {"type": "result", "subtype": "success", "is_error": False, "result": "   "}
        parsed = adapters.parse_claude(
            self._job(), _outcome(stdout=json.dumps(payload)), model=None
        )
        self.assertEqual(parsed.error_code, "cli_error")

    def test_timeout_wins_over_everything(self) -> None:
        parsed = adapters.parse_claude(
            self._job(), _outcome(code=None, stdout="", timed_out=True), model=None
        )
        self.assertEqual(parsed.error_code, "timeout")


class ClassifyTest(unittest.TestCase):
    def test_auth_and_rate_limits_are_distinguished(self) -> None:
        self.assertEqual(adapters.classify("You are not logged in. Run /login"), "auth_required")
        self.assertEqual(adapters.classify("HTTP 401 Unauthorized"), "auth_required")
        self.assertEqual(adapters.classify("rate limit exceeded"), "rate_limited")
        self.assertEqual(adapters.classify("429 Too Many Requests"), "rate_limited")
        self.assertEqual(adapters.classify("weekly usage limit reached"), "rate_limited")
        self.assertEqual(adapters.classify("segmentation fault"), "cli_error")

    def test_result_never_carries_raw_stderr(self) -> None:
        """分类只在本地看 stderr，回给服务端的只有枚举值。"""
        job = adapters.Job(argv=[], stdin_text="")
        secret = r"C:\Users\alice\.claude\.credentials.json 读取失败"
        parsed = adapters.parse_claude(job, _outcome(code=2, stderr=secret), model=None)
        self.assertNotIn("alice", str(parsed))
        self.assertIn(parsed.error_code, {"cli_error", "auth_required", "rate_limited"})


if __name__ == "__main__":
    unittest.main()
