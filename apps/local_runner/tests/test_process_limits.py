"""子进程的超时、输出上限与清理。

用真实子进程跑（起的是本机的 `python -c`，不碰任何 CLI、不花任何额度）。
这三件事只有真跑一遍才算验过：mock 出来的超时不会验证"管道抽干了没有"。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

from apps.local_runner.process import MAX_STDOUT_BYTES, Outcome, Runner


class ProcessLimitsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = Runner()
        self.cwd = tempfile.mkdtemp(prefix="local-runner-test-")

    def _run(self, code: str, *, timeout: int = 30, stdin_text: str = "") -> Outcome:
        return self.runner.run(
            [sys.executable, "-c", code],
            stdin_text=stdin_text,
            cwd=self.cwd,
            env=dict(os.environ),
            timeout_seconds=timeout,
        )

    def test_stdin_is_delivered_verbatim(self) -> None:
        outcome = self._run(
            "import sys;sys.stdout.write(sys.stdin.read())",
            stdin_text="& | ; `id` %PATH% 换行\n第二行",
        )
        self.assertEqual(outcome.returncode, 0)
        self.assertIn("`id`", outcome.stdout)
        self.assertIn("第二行", outcome.stdout)

    def test_timeout_kills_and_reports(self) -> None:
        started = time.monotonic()
        outcome = self._run("import time;time.sleep(60)", timeout=3)
        elapsed = time.monotonic() - started
        self.assertTrue(outcome.timed_out)
        self.assertLess(elapsed, 40, "超时后没有及时杀掉进程")

    def test_a_flood_of_output_is_bounded_and_does_not_hang(self) -> None:
        """子进程狂刷 stdout 时必须两件事同时成立：截断，且不卡死。

        只截断不抽干的写法会让子进程写满管道缓冲区后阻塞，
        于是"输出太多"变成"永远不返回"——比溢出更难查。
        """
        code = (
            "import sys\n"
            "for _ in range(4000):\n"
            "    sys.stdout.write('x' * 1024)\n"
            "sys.stdout.flush()\n"
        )
        outcome = self._run(code, timeout=60)
        self.assertFalse(outcome.timed_out)
        self.assertEqual(outcome.returncode, 0)
        self.assertLessEqual(len(outcome.stdout.encode("utf-8")), MAX_STDOUT_BYTES)

    def test_cwd_is_the_given_directory(self) -> None:
        """每条请求跑在一个全新的空目录里，CLI 拿不到仓库上下文。"""
        outcome = self._run("import os;print(os.getcwd())")
        self.assertEqual(os.path.realpath(outcome.stdout.strip()), os.path.realpath(self.cwd))


if __name__ == "__main__":
    unittest.main()
