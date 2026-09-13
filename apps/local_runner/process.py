"""跑一个 CLI 子进程：定长输出、有限超时、超时杀进程树。

三条都不是可选项：

* **定长输出**。CLI 出问题时会刷屏（重试日志、进度条、堆栈），
  `communicate()` 会把它整个读进内存。这里读到上限就只丢弃不再累积，
  但**继续把管道抽干**——不抽干的话子进程写满管道缓冲区就卡死，
  于是"输出太多"变成"永远不返回"。
* **有限超时**。上限来自服务端下发的 `timeout_seconds`。
* **杀进程树**。Windows 上 `Popen.kill()` 只杀直接子进程；`claude` /
  `codex` 会拉起 node 或别的子进程，只杀父进程会留下孤儿继续跑、
  继续占额度。用 `taskkill /T /F`。POSIX 上用进程组。
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import threading
from dataclasses import dataclass
from typing import IO

#: stdout 上限。分镜产出是 JSON，几十 KB 量级；1 MiB 已经宽得离谱。
MAX_STDOUT_BYTES = 1024 * 1024
#: stderr 只用于本地分类（认证失败 / 限流），永远不回传服务端，给得更小。
MAX_STDERR_BYTES = 64 * 1024


@dataclass(frozen=True)
class Outcome:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool


def _drain(stream: IO[bytes] | None, cap: int, sink: list[bytes]) -> None:
    """把管道抽干，但只留前 `cap` 个字节。"""
    if stream is None:
        return
    kept = 0
    try:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
            if kept < cap:
                room = cap - kept
                sink.append(chunk[:room])
                kept += min(room, len(chunk))
    except (ValueError, OSError):
        # 进程被杀时管道会被提前关掉，这不是错误。
        pass
    finally:
        with contextlib.suppress(OSError):
            stream.close()


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        # 只用 pid，不拼任何来自网络的字符串；shell=False。
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
            timeout=30,
        )
    else:
        import signal

        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


class Runner:
    """一次只跑一个子进程。`cancel()` 可以从别的线程（Ctrl-C 处理）打断。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[bytes] | None = None

    def cancel(self) -> None:
        with self._lock:
            proc = self._proc
        if proc is not None:
            _kill_tree(proc)

    def run(
        self,
        argv: list[str],
        *,
        stdin_text: str,
        cwd: str,
        env: dict[str, str],
        timeout_seconds: int,
    ) -> Outcome:
        """跑一次。`argv` 是列表，`shell=False`——命令行不经过任何 shell。"""
        # argv 是列表且 shell=False：命令行不经过任何 shell 解释。
        # 两个分支只差 `start_new_session`（POSIX 上自成进程组，超时能整组杀掉；
        # Windows 上没有这个概念，靠 taskkill /T）。写成两条而不是 **kwargs，
        # 是因为后者在 mypy strict 下必须挂 type: ignore，而那会把真正的
        # 参数错误一起盖住。
        if os.name == "nt":
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                env=env,
                shell=False,
            )
        else:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                env=env,
                shell=False,
                start_new_session=True,
            )
        with self._lock:
            self._proc = proc

        out_chunks: list[bytes] = []
        err_chunks: list[bytes] = []
        threads = [
            threading.Thread(
                target=_drain, args=(proc.stdout, MAX_STDOUT_BYTES, out_chunks), daemon=True
            ),
            threading.Thread(
                target=_drain, args=(proc.stderr, MAX_STDERR_BYTES, err_chunks), daemon=True
            ),
        ]
        for thread in threads:
            thread.start()

        timed_out = False
        try:
            if proc.stdin is not None:
                try:
                    proc.stdin.write(stdin_text.encode("utf-8"))
                except (BrokenPipeError, OSError):
                    pass
                finally:
                    with contextlib.suppress(OSError):
                        proc.stdin.close()
            try:
                proc.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_tree(proc)
        finally:
            for thread in threads:
                thread.join(timeout=10)
            with self._lock:
                self._proc = None

        return Outcome(
            returncode=proc.returncode,
            stdout=b"".join(out_chunks).decode("utf-8", errors="replace"),
            stderr=b"".join(err_chunks).decode("utf-8", errors="replace"),
            timed_out=timed_out,
        )
