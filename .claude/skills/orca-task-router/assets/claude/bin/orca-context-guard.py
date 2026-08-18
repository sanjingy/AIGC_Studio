#!/usr/bin/env python3
"""Warn an Orca Worker when its context crosses the handoff threshold.

Input : a PostToolUse hook payload on stdin (needs transcript_path, session_id).
Output: a hookSpecificOutput JSON with additionalContext, or nothing.
Exit   : always 0. A guard that breaks the session is worse than no guard.

Claude Code exposes no token counts to hooks, but the transcript JSONL carries
`message.usage` per assistant turn. Summing the input side of the last usage
record reconstructs what /context shows.
"""

import json
import os
import sys

DEFAULT_THRESHOLD = 300_000
DEFAULT_PERCENT = 30
# Real transcripts have shown gaps of up to 1.55MB between adjacent usage
# records; a smaller tail window makes the guard silently skip those turns.
TAIL_BYTES = 4 * 1024 * 1024
STATE_DIR = os.environ.get("TMPDIR", "/tmp")


def window_facts(session_id):
    """Read what the status line published for this session.

    Hooks receive no token or context-window data. The transcript carries
    usage but its `model` field drops the [1m] suffix, so the window size
    cannot be inferred from it -- a 1M session and a 200k session look
    identical until one of them exceeds 200k. The status line does get
    `context_window_size`, so it writes both numbers out for us.
    """
    if not session_id:
        return None
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")[:64]
    path = os.path.join(STATE_DIR, f"claude-ctx-{safe}")
    try:
        with open(path, encoding="utf-8") as fh:
            parts = fh.read().split()
        window, used_pct = int(parts[0]), float(parts[1])
    except Exception:
        return None
    if window <= 0:
        return None
    return window, used_pct


def read_payload():
    try:
        raw = sys.stdin.read()
    except Exception:
        return None
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def last_usage(path):
    """Scan backwards for the newest assistant turn that reported usage."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > TAIL_BYTES:
                fh.seek(size - TAIL_BYTES)
                fh.readline()  # drop the partial line
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except Exception:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line or '"usage"' not in line:
            continue
        try:
            record = json.loads(line)
        except Exception:
            continue
        message = record.get("message")
        if isinstance(message, dict) and isinstance(message.get("usage"), dict):
            if has_real_usage(message["usage"]):
                return message["usage"]
    return None


def used_tokens(usage):
    """Sum the input side; these three keys partition the prompt exactly."""
    total = 0
    for key in ("input_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens"):
        value = usage.get(key)
        if isinstance(value, (int, float)):
            total += int(value)
    return total


def has_real_usage(usage):
    """Synthetic records written on API errors carry all-zero usage."""
    return used_tokens(usage) > 0


def threshold():
    raw = os.environ.get("ORCA_CONTEXT_HANDOFF_THRESHOLD", "")
    try:
        value = int(raw)
        return value if value > 0 else DEFAULT_THRESHOLD
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD


def percent():
    raw = os.environ.get("ORCA_CONTEXT_HANDOFF_PERCENT", "")
    try:
        value = float(raw)
        return value if 0 < value < 100 else DEFAULT_PERCENT
    except (TypeError, ValueError):
        return DEFAULT_PERCENT


def already_warned(session_id, limit):
    """Fire once per threshold per session; the hook runs on every tool call."""
    if not session_id:
        return False
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")[:64]
    marker = os.path.join(STATE_DIR, f"orca-ctx-guard-{safe}-{limit}")
    if os.path.exists(marker):
        return True
    try:
        with open(marker, "w") as fh:
            fh.write("1")
    except Exception:
        pass  # Cannot persist: warn again rather than stay silent.
    return False


def emit(used, limit):
    message = (
        f"[Orca上下文守卫] 当前会话已用约 {used:,} tokens，超过 {limit:,} 的交接阈值。\n"
        "现在按 orca-task-router 的 Worker 交接协议处理，不要继续接新工作：\n"
        "1. 停在当前这步，不要开始新的子任务。\n"
        "2. 写交接文档，七个字段：目标、已完成、剩余、下一步第一个动作、"
        "已排除的假设、风险、验收标准。\n"
        "3. 把交接文档回报给 Lead，说明是上下文触顶交接而不是任务失败：\n"
        "   前言里给了 taskId 和 dispatchId 的：orca orchestration send "
        "--type escalation --subject 'Context handoff' --body '<交接文档>' "
        "--task-id <taskId> --dispatch-id <dispatchId> --json\n"
        "   两个 ID 都必须带，Lead 要靠它们收口这条 Dispatch。\n"
        "   没有前言、由 terminal send 直接送来的：留在本终端输出交接文档，等 Lead 轮询取走\n"
        "   不要用 --type worker_done，那会把 Task 和 Dispatch 一起标成已完成。\n"
        "4. 回报后结束当前回合，停在提示符等待。不要退出会话，不要自己创建新 "
        "Terminal。会话关掉 Lead 就读不到你的输出、也没法追问交接细节，什么时候"
        "回收由 Lead 决定。\n"
        "你是Lead而不是Worker时，改为提醒用户手动压缩或交接，不要自行开新会话。"
    )
    json.dump({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": message,
        }
    }, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


def main():
    payload = read_payload()
    if not payload:
        return
    # A subagent's PostToolUse carries the PARENT's transcript_path and
    # session_id, but injects into the subagent. Acting here would warn the
    # wrong context and burn the parent's marker, silencing the main thread
    # for good. Orca Workers dispatch subagents often, so this is the common
    # path, not an edge case.
    if payload.get("agent_id"):
        return
    session_id = payload.get("session_id")

    facts = window_facts(session_id)
    if facts:
        # Percentage path: works on 200k and 1M sessions alike. A fixed token
        # threshold tuned for 1M can never fire inside a 200k window.
        window, used_pct = facts
        pct_limit = percent()
        if used_pct < pct_limit:
            return
        used = int(window * used_pct / 100)
        limit = int(window * pct_limit / 100)
        marker = "pct%d" % int(pct_limit)
    else:
        # Fallback when the status line has not published yet. Absolute counts
        # cannot distinguish window sizes, so this only catches big sessions.
        path = payload.get("transcript_path")
        if not path or not os.path.isfile(path):
            return
        usage = last_usage(path)
        if not usage:
            return
        used = used_tokens(usage)
        limit = threshold()
        if used < limit:
            return
        marker = str(limit)

    if already_warned(session_id, marker):
        return
    emit(used, limit)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # Never break the session over a monitoring failure.
    sys.exit(0)
