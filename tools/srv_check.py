"""在香港测试机上跑后端门禁。本地不需要 Docker。

    python tools/srv_check.py pytest
    python tools/srv_check.py "ruff check ."
    python tools/srv_check.py mypy
    python tools/srv_check.py all          # ruff + format + mypy + pytest

做了什么：把**当前工作区**（含未提交改动）打包传到服务器的部署目录，
在那边的 api 容器里跑命令，跑完把服务器检出恢复干净。

为什么不用 rsync：这台 Windows 上没有 rsync。`git ls-files -co --exclude-standard`
拿到的文件列表天然排除了 .gitignore 里的东西（node_modules / .next / .env），
比手写排除规则可靠——它跟着 .gitignore 走，不会因为有人加了新目录而漏掉。

为什么直接传进部署目录而不是另起一份：`apps/` 等目录在 compose 里是 bind mount，
文件落地即生效，不用重建镜像。代价是把部署检出弄脏了，所以跑完必须恢复——
否则下一次 `aigc_deploy.sh` 的 `git pull --ff-only` 会失败。

**并发**：服务器上只有一套容器和一个数据库，同一时刻只能有一个人跑。
这条今天不是限制——账号速率限制本来就逼着一次只跑一个 Claude Worker。
真要并发得给每个 Worker 起一套容器，而那台机上还跑着别人的游戏服，内存不够。
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tarfile
import time

import paramiko

HOST = os.environ.get("SRV_HOST", "38.76.215.147")
KEY = os.path.expanduser("~/.ssh/id_ed25519")
REMOTE = "/opt/aigc_studio"

# 跑完必须恢复检出，否则下次部署的 git pull --ff-only 会撞上脏文件。
RESTORE = (
    f"cd {REMOTE} && git checkout -- . && git clean -fd -e docker-compose.override.yml -e .env"
)

COMMANDS = {
    "pytest": "docker compose exec -T api pytest",
    "ruff": "docker compose exec -T api ruff check .",
    "format": "docker compose exec -T api ruff format --check .",
    "mypy": "docker compose exec -T api mypy apps worker packages agents adapters skills",
}
COMMANDS["all"] = " && ".join(
    [COMMANDS["ruff"], COMMANDS["format"], COMMANDS["mypy"], COMMANDS["pytest"]]
)


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line for line in out.splitlines() if line.strip()]


def _tarball(paths: list[str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in paths:
            if os.path.isfile(path):
                tar.add(path, arcname=path)
    return buf.getvalue()


def main() -> int:
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    command = COMMANDS.get(what, what)

    paths = _tracked_files()
    blob = _tarball(paths)
    print(f"[srv] 打包 {len(paths)} 个文件，{len(blob) / 1024:.0f} KB", flush=True)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username="root", key_filename=KEY, timeout=30)

    try:
        sftp = client.open_sftp()
        remote_tar = "/tmp/aigc_sync.tgz"
        with sftp.open(remote_tar, "wb") as fh:
            fh.write(blob)
        sftp.close()
        print("[srv] 已上传，展开到部署目录", flush=True)

        # 先恢复干净再展开：上一轮如果异常中断，检出可能还是脏的。
        script = (
            f"{RESTORE} >/dev/null 2>&1; "
            f"cd {REMOTE} && tar xzf {remote_tar} && "
            f"echo '[srv] 开始执行: {what}' && "
            f"cd {REMOTE} && {command}"
        )
        started = time.time()
        _, out, err = client.exec_command(script, timeout=3600, get_pty=False)
        for line in iter(out.readline, ""):
            print(line, end="", flush=True)
        code = out.channel.recv_exit_status()
        tail = err.read().decode(errors="replace")
        if tail.strip():
            print("[stderr]", tail[-4000:], flush=True)
        print(f"[srv] 退出码 {code}，耗时 {time.time() - started:.0f}s", flush=True)
        return code
    finally:
        # 无论成败都恢复，别把部署检出留成脏的。
        _, out, _ = client.exec_command(RESTORE, timeout=120)
        out.channel.recv_exit_status()
        client.close()
        print("[srv] 服务器检出已恢复", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
