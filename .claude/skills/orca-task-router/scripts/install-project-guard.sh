#!/usr/bin/env bash
# Input: one or more project directories; --dry-run to preview.
# Output: merged .claude/settings.json carrying the context guard hook.
# Exit code: 0 on success; 2 on validation failure.
#
# Why project level: every launch path on this machine funnels into claude-cc,
# which must pass --settings to inject the API Key Helper. That flag shadows
# the hooks in ~/.claude/settings.json, so a global hook never fires. Project
# settings are read on top of it and do fire. Verified 2026-08-01.
#
# CORRECTION 2026-08-05, Claude Code 2.1.222: the "global hook never fires"
# half is wrong. A PreToolUse hook declared in the profile settings.json does
# fire through the full claude-cc path -- --settings merges rather than
# shadows. This guard stays at project level because that is where it already
# lives and moving it buys nothing, not because user level does not work.
# orca-model-guard.py is installed at user level for exactly that reason.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
GUARD_PATH='$HOME/.config/claude-cc/orca-context-guard.py'

DRY_RUN=0
TARGETS=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '1,10p' "$0"; exit 0 ;;
    -*) printf 'Unknown argument: %s\n' "$1" >&2; exit 64 ;;
    *) TARGETS+=("$1") ;;
  esac
  shift
done

[ "${#TARGETS[@]}" -gt 0 ] || { printf 'Usage: %s [--dry-run] <project-dir>...\n' "${0##*/}" >&2; exit 64; }

for target in "${TARGETS[@]}"; do
  if [ ! -d "$target" ]; then
    printf '[SKIP] not a directory: %s\n' "$target" >&2
    continue
  fi
  DRY_RUN="$DRY_RUN" GUARD_PATH="$GUARD_PATH" /usr/bin/env python3 - "$target" <<'PY'
import json, os, sys

target = sys.argv[1]
dry = os.environ.get("DRY_RUN") == "1"
guard = os.environ["GUARD_PATH"]
command = ('if [ -r "%s" ]; then python3 "%s"; '
           'else { command -p cat 2>/dev/null || cat; } >/dev/null 2>&1 || :; fi') % (guard, guard)

path = os.path.join(target, ".claude", "settings.json")
data = {}
if os.path.exists(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        print("[ERROR] existing settings.json is not valid JSON: %s" % path)
        raise SystemExit(2)
    if not isinstance(data, dict):
        print("[ERROR] existing settings.json is not an object: %s" % path)
        raise SystemExit(2)

hooks = data.setdefault("hooks", {})
post = hooks.setdefault("PostToolUse", [])
if any("orca-context-guard" in json.dumps(entry) for entry in post):
    print("[SKIP] guard already present: %s" % path)
    raise SystemExit(0)

post.append({"matcher": "*", "hooks": [{"type": "command", "command": command, "timeout": 10}]})

if dry:
    print("[PLAN] add context guard to %s" % path)
    raise SystemExit(0)

os.makedirs(os.path.dirname(path), exist_ok=True)
tmp = path + ".orca-tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(data, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
os.replace(tmp, path)
print("[DONE] guard installed: %s" % path)
PY
done
