#!/usr/bin/env python3
"""Report the Codex weekly rate-limit level so the Lead can pick a tier.

Input : nothing (reads ~/.codex/sessions); --json for machine output.
Output: used percent, time to reset, and the tier that level implies.
Exit  : 0 on success, 1 when no usage record can be found.

Codex has no `usage` subcommand. The number the TUI status line shows arrives
on every turn inside the rollout log as rate_limits.primary, so the freshest
record in the newest session file is the same number, readable without a live
terminal. window_minutes is 10080 -- a 7 day window.

Tier thresholds exist because reasoning effort turned out not to be a cost
lever at all: on the sessions measured 2026-08-05, output was 0.4% of total
tokens and input 99.6%. Downshifting effort saves nothing, so the tiers switch
model instead, and effort stays pinned at each model's deepest setting.
"""

import glob
import json
import os
import sys
import time

SESSION_ROOT = os.path.expanduser("~/.codex/sessions")
TAIL_BYTES = 2 * 1024 * 1024
# Overridable so a tighter week can be handled without editing the Skill.
TERRA_AT = float(os.environ.get("ORCA_CODEX_TERRA_AT", "60"))
LUNA_AT = float(os.environ.get("ORCA_CODEX_LUNA_AT", "85"))

TIERS = [
    (LUNA_AT, "luna", "gpt-5.6-luna", "max"),
    (TERRA_AT, "terra", "gpt-5.6-terra", "max"),
    (0.0, "sol", "gpt-5.6-sol", "ultra"),
]


def newest_sessions(limit=12):
    """Newest rollout logs first. Sorted by mtime, not by name: a session
    started before midnight keeps writing into yesterday's directory."""
    paths = glob.glob(os.path.join(SESSION_ROOT, "*", "*", "*", "*.jsonl"))
    paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return paths[:limit]


def last_rate_limit(path):
    """Scan the tail backwards for the freshest token_count event."""
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
        if '"rate_limits"' not in line:
            continue
        try:
            payload = json.loads(line).get("payload") or {}
        except Exception:
            continue
        limits = payload.get("rate_limits") or {}
        primary = limits.get("primary") or {}
        if primary.get("used_percent") is not None:
            return {
                "used_percent": float(primary["used_percent"]),
                "window_minutes": primary.get("window_minutes"),
                "resets_at": primary.get("resets_at"),
                "plan_type": limits.get("plan_type"),
                "source": path,
            }
    return None


def tier_for(used_percent):
    for floor, name, model, effort in TIERS:
        if used_percent >= floor:
            return {"tier": name, "model": model, "effort": effort}
    return {"tier": "sol", "model": "gpt-5.6-sol", "effort": "ultra"}


def main():
    # Machine-readable thresholds for check-consistency.sh. Grepping the source
    # for the literal "60" also matched the 3600 in the seconds-to-hours divisor,
    # so the checker asks the script instead of guessing from text.
    if "--thresholds" in sys.argv[1:]:
        print("%g %g" % (TERRA_AT, LUNA_AT))
        return 0

    facts = None
    for path in newest_sessions():
        facts = last_rate_limit(path)
        if facts:
            break
    if not facts:
        print("no Codex usage record found under ~/.codex/sessions", file=sys.stderr)
        return 1

    facts.update(tier_for(facts["used_percent"]))
    resets_at = facts.get("resets_at")
    if isinstance(resets_at, (int, float)):
        facts["resets_in_hours"] = round(max(0.0, resets_at - time.time()) / 3600, 1)

    if "--json" in sys.argv[1:]:
        json.dump(facts, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    used = facts["used_percent"]
    line = "codex weekly: %.0f%% used, %.0f%% left" % (used, 100 - used)
    if "resets_in_hours" in facts:
        line += ", resets in %.1fh" % facts["resets_in_hours"]
    print(line)
    print("tier: %s  (--model %s -c 'model_reasoning_effort=\"%s\"')"
          % (facts["tier"], facts["model"], facts["effort"]))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never let a reporting script break a dispatch
        print("codex usage probe failed: %s" % exc, file=sys.stderr)
        sys.exit(1)
