#!/usr/bin/env bash
# Input: --dry-run (default), --apply, or --verify.
# Output: planned, installed, or verified Claude rules, agent definitions,
#         lead prompt, lead model, lead effort, and Claude/Codex skill symlinks.
# Exit code: 0 on success; 2 on validation failure; 4 on install failure.

set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"
ASSET_ROOT="$SKILL_DIR/assets/claude"

APPLY=0
VERIFY_ONLY=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) APPLY=0 ;;
    --apply) APPLY=1 ;;
    --verify) VERIFY_ONLY=1 ;;
    -h|--help)
      sed -n '1,6p' "$0"
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      exit 64
      ;;
  esac
  shift
done

CLAUDE_HOME="$HOME/.claude"
CODEX_SKILL_HOME="$HOME/.Codex/skills"
CLAUDE_SKILL_LINK="$CLAUDE_HOME/skills/orca-task-router"
CODEX_SKILL_LINK="$CODEX_SKILL_HOME/orca-task-router"
RULE_TARGET="$CLAUDE_HOME/rules/orca-task-routing.md"
CONFIG_ROOT="$HOME/.config/claude-cc"
PROMPT_TARGET="$CONFIG_ROOT/orca-lead-system-prompt.txt"
MODEL_TARGET="$CONFIG_ROOT/orca-lead-model"
EFFORT_TARGET="$CONFIG_ROOT/orca-lead-effort"
GUARD_TARGET="$CONFIG_ROOT/orca-context-guard.py"
MODEL_GUARD_TARGET="$CONFIG_ROOT/orca-model-guard.py"
CODEX_USAGE_TARGET="$CONFIG_ROOT/orca-codex-usage.py"
# The statusline is the guard's only source of context-window size; hooks are
# handed no window data. Keeping it outside version control meant a new machine
# got a guard that silently degraded to a fixed token threshold.
STATUSLINE_TARGET="$CLAUDE_HOME/statusline-command.sh"
STABILITY_HOME="$HOME/.claude-stability"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$STABILITY_HOME/backups/orca-task-router-$STAMP"
BACKED_UP=""

log() { printf '%s\n' "$1"; }
plan() { printf '[PLAN] %s\n' "$1"; }
done_msg() { printf '[DONE] %s\n' "$1"; }
skip() { printf '[SKIP] %s\n' "$1"; }
warn() { printf '[WARN] %s\n' "$1"; }
fail() { printf '[ERROR] %s\n' "$1" >&2; exit "${2:-2}"; }

for required in \
  "$SKILL_DIR/SKILL.md" \
  "$ASSET_ROOT/rules/orca-task-routing.md" \
  "$ASSET_ROOT/orca-lead-system-prompt.txt" \
  "$ASSET_ROOT/orca-lead-model" \
  "$ASSET_ROOT/orca-lead-effort" \
  "$ASSET_ROOT/bin/orca-context-guard.py" \
  "$ASSET_ROOT/bin/orca-model-guard.py" \
  "$ASSET_ROOT/bin/orca-codex-usage.py" \
  "$ASSET_ROOT/statusline-command.sh"; do
  [ -r "$required" ] || fail "Missing source asset: $required"
done

# Agent definitions are discovered, not listed. check-consistency.sh finds them
# with the same glob, and a listed-here-but-forgotten-there agent would pass every
# check while never being installed: the checker would validate the source file,
# verify_all would only look at the names it knew about, and all three gates would
# report green on a teammate that does not exist on disk.
AGENT_SOURCES=()
for agent_src in "$ASSET_ROOT"/agents/*.md; do
  [ -r "$agent_src" ] || continue
  AGENT_SOURCES+=("$agent_src")
done
[ "${#AGENT_SOURCES[@]}" -gt 0 ] || fail "No agent definitions found under $ASSET_ROOT/agents"

backup_once() {
  local path="$1" rel dest
  [ -e "$path" ] || [ -L "$path" ] || return 0
  case "$BACKED_UP" in
    *"|$path|"*) return 0 ;;
  esac
  BACKED_UP="${BACKED_UP}|${path}|"
  rel="${path#/}"
  dest="$BACKUP_DIR/$rel"
  mkdir -p "$(dirname "$dest")"
  cp -a "$path" "$dest"
  done_msg "backed up ${path/#$HOME/~}"
}

install_file() {
  local source="$1" target="$2" tmp
  if [ -f "$target" ] && cmp -s "$source" "$target"; then
    chmod 600 "$target"
    skip "unchanged: ${target/#$HOME/~}"
    return
  fi
  backup_once "$target"
  mkdir -p "$(dirname "$target")"
  tmp="$(mktemp "$(dirname "$target")/.orca-router.XXXXXX")"
  cp "$source" "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$target"
  done_msg "installed ${target/#$HOME/~}"
}

install_link() {
  local target="$1" tmp_dir
  if [ -L "$target" ]; then
    if [ "$(readlink "$target")" = "$SKILL_DIR" ]; then
      skip "unchanged: ${target/#$HOME/~}"
      return
    fi
  fi
  if [ -d "$target" ] && [ ! -L "$target" ]; then
    fail "Refusing to replace a real directory: ${target/#$HOME/~}" 4
  fi
  backup_once "$target"
  mkdir -p "$(dirname "$target")"
  tmp_dir="$(mktemp -d "$(dirname "$target")/.orca-task-router-link.XXXXXX")"
  ln -s "$SKILL_DIR" "$tmp_dir/link"
  rm -f "$target"
  mv "$tmp_dir/link" "$target"
  rmdir "$tmp_dir"
  done_msg "linked ${target/#$HOME/~}"
}

verify_file() {
  local source="$1" target="$2"
  if [ -f "$target" ] && cmp -s "$source" "$target"; then
    done_msg "verified ${target/#$HOME/~}"
  else
    warn "missing or changed: ${target/#$HOME/~}"
    return 1
  fi
}

verify_link() {
  local target="$1" resolved
  if [ ! -L "$target" ]; then
    warn "not a symlink: ${target/#$HOME/~}"
    return 1
  fi
  resolved="$(readlink "$target")"
  if [ "$resolved" = "$SKILL_DIR" ]; then
    done_msg "verified ${target/#$HOME/~}"
  else
    warn "unexpected target for ${target/#$HOME/~}: $resolved"
    return 1
  fi
}

verify_all() {
  local failed=0
  verify_link "$CLAUDE_SKILL_LINK" || failed=1
  verify_link "$CODEX_SKILL_LINK" || failed=1
  verify_file "$ASSET_ROOT/rules/orca-task-routing.md" "$RULE_TARGET" || failed=1
  for agent_src in "${AGENT_SOURCES[@]}"; do
    verify_file "$agent_src" "$CLAUDE_HOME/agents/${agent_src##*/}" || failed=1
  done
  verify_file "$ASSET_ROOT/orca-lead-system-prompt.txt" "$PROMPT_TARGET" || failed=1
  verify_file "$ASSET_ROOT/orca-lead-model" "$MODEL_TARGET" || failed=1
  verify_file "$ASSET_ROOT/orca-lead-effort" "$EFFORT_TARGET" || failed=1
  verify_file "$ASSET_ROOT/bin/orca-context-guard.py" "$GUARD_TARGET" || failed=1
  verify_file "$ASSET_ROOT/bin/orca-model-guard.py" "$MODEL_GUARD_TARGET" || failed=1
  verify_file "$ASSET_ROOT/bin/orca-codex-usage.py" "$CODEX_USAGE_TARGET" || failed=1
  verify_file "$ASSET_ROOT/statusline-command.sh" "$STATUSLINE_TARGET" || failed=1
  verify_model_guard_hook || failed=1
  [ "$failed" -eq 0 ] || fail "Orca task router verification failed." 4
}

# Every settings.json a session on this box can read. ~/.claude/settings.json
# serves the legacy profile (claude-cc unsets CLAUDE_CONFIG_DIR for it); each
# ~/.claude-profiles/<name>/settings.json serves a named profile. The two are
# separate real files -- unlike agents/ and skills/, settings.json is not
# symlinked into the profile -- so wiring only one leaves the other unguarded.
#
# Paths are emitted whether or not the file exists yet. A fresh machine has no
# settings.json at all, and treating that as "nothing to guard" would ship a
# guard that is installed but never runs. The writer creates the file instead.
settings_files() {
  local profile_dir
  printf '%s\n' "$CLAUDE_HOME/settings.json"
  for profile_dir in "$HOME"/.claude-profiles/*/; do
    [ -d "$profile_dir" ] || continue
    printf '%s\n' "${profile_dir}settings.json"
  done
  return 0
}

# Why user level, not per project like the context guard: this guard has to
# cover a Lead that spawns teammates from any directory, including a repo
# nobody wired up. install-project-guard.sh carries a comment claiming a
# user-level hook never fires because claude-cc passes --settings for the API
# Key Helper. Retested on Claude Code 2.1.222 (2026-08-05): a PreToolUse hook
# declared in the profile settings.json DID fire through the full claude-cc
# launch path. --settings merges, it does not shadow.
install_model_guard_hook() {
  local target
  while IFS= read -r target; do
    [ -n "$target" ] || continue
    backup_once "$target"
    GUARD_PATH='$HOME/.config/claude-cc/orca-model-guard.py' \
      /usr/bin/env python3 - "$target" <<'PY'
import json, os, sys

path = sys.argv[1]
guard = os.environ["GUARD_PATH"]
command = ('if [ -r "%s" ]; then python3 "%s"; '
           'else { command -p cat 2>/dev/null || cat; } >/dev/null 2>&1 || :; fi') % (guard, guard)

data = {}
if os.path.exists(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        print("[ERROR] settings.json is not valid JSON: %s" % path)
        raise SystemExit(2)
    if not isinstance(data, dict):
        print("[ERROR] settings.json is not an object: %s" % path)
        raise SystemExit(2)

pre = data.setdefault("hooks", {}).setdefault("PreToolUse", [])
if any("orca-model-guard" in json.dumps(entry) for entry in pre):
    print("[SKIP] model guard already wired: %s" % path)
    raise SystemExit(0)

# matcher "Agent": the tool was renamed from Task in 2.1.63. Both the spawn
# path and the model parameter live on this one tool, so one matcher covers
# foreground and background dispatches alike.
pre.append({"matcher": "Agent",
            "hooks": [{"type": "command", "command": command, "timeout": 10}]})

mode = (os.stat(path).st_mode & 0o777) if os.path.exists(path) else 0o600
os.makedirs(os.path.dirname(path), exist_ok=True)
tmp = path + ".orca-tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(data, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
os.chmod(tmp, mode)
os.replace(tmp, path)
print("[DONE] model guard wired: %s" % path)
PY
  done <<EOF
$(settings_files)
EOF
}

verify_model_guard_hook() {
  local target missing=0
  # settings_files always yields at least ~/.claude/settings.json, and the
  # writer creates it when absent. So a missing file here is a failed install,
  # not a fresh machine.
  while IFS= read -r target; do
    [ -n "$target" ] || continue
    if grep -q "orca-model-guard" "$target" 2>/dev/null; then
      done_msg "verified model guard hook in ${target/#$HOME/~}"
    else
      warn "model guard hook missing from ${target/#$HOME/~}"
      missing=1
    fi
  done <<EOF
$(settings_files)
EOF
  [ "$missing" -eq 0 ]
}

CONSISTENCY="$SCRIPT_DIR/check-consistency.sh"

if [ "$VERIFY_ONLY" -eq 1 ]; then
  log "# Orca Task Router Verification"
  verify_all
  if [ -x "$CONSISTENCY" ]; then
    "$CONSISTENCY" >/dev/null || fail "Skill content drifted from the canonical facts; run scripts/check-consistency.sh" 4
    done_msg "canonical facts and Skill content agree"
  fi
  exit 0
fi

log "# Orca Task Router Setup"
log "[INFO] source: $SKILL_DIR"

if [ "$APPLY" -eq 0 ]; then
  log "[MODE] dry-run. No files will be changed."
  plan "link the Skill into ~/.claude/skills and ~/.Codex/skills"
  plan "install the always-on Claude routing rule"
  plan "install the Agent Teams teammate definitions"
  plan "install the lead model, effort, and system prompt used by claude-cc-teams"
  plan "install the worker context guard and the statusline that feeds it"
  plan "install the model guard and wire it into every settings.json as a PreToolUse/Agent hook"
  plan "install the Codex quota probe the tier table is written against"
  exit 0
fi

mkdir -p "$CLAUDE_HOME/skills" "$CODEX_SKILL_HOME" "$CLAUDE_HOME/rules" "$CLAUDE_HOME/agents" "$CONFIG_ROOT"
chmod 700 "$CLAUDE_HOME/skills" "$CODEX_SKILL_HOME" "$CLAUDE_HOME/rules" "$CLAUDE_HOME/agents" "$CONFIG_ROOT"

install_link "$CLAUDE_SKILL_LINK"
install_link "$CODEX_SKILL_LINK"
install_file "$ASSET_ROOT/rules/orca-task-routing.md" "$RULE_TARGET"
for agent_src in "${AGENT_SOURCES[@]}"; do
  install_file "$agent_src" "$CLAUDE_HOME/agents/${agent_src##*/}"
done
install_file "$ASSET_ROOT/orca-lead-system-prompt.txt" "$PROMPT_TARGET"
install_file "$ASSET_ROOT/orca-lead-model" "$MODEL_TARGET"
install_file "$ASSET_ROOT/orca-lead-effort" "$EFFORT_TARGET"
install_file "$ASSET_ROOT/bin/orca-context-guard.py" "$GUARD_TARGET"
install_file "$ASSET_ROOT/bin/orca-model-guard.py" "$MODEL_GUARD_TARGET"
install_file "$ASSET_ROOT/bin/orca-codex-usage.py" "$CODEX_USAGE_TARGET"
install_file "$ASSET_ROOT/statusline-command.sh" "$STATUSLINE_TARGET"
install_model_guard_hook

verify_all

# The statusline only runs if settings.json points at it. Installing the script
# without that wiring leaves the guard blind to window size on a fresh machine.
SETTINGS_JSON="$CLAUDE_HOME/settings.json"
if [ -r "$SETTINGS_JSON" ] && ! grep -q "statusline-command.sh" "$SETTINGS_JSON"; then
  warn "~/.claude/settings.json does not point statusLine at statusline-command.sh"
  warn "the context guard will fall back to a fixed token threshold until it does"
fi

# Cross-Skill version gate. The launcher lives in claude-env-stability, so a
# lone upgrade here would install the effort config that nothing reads.
TEAMS_LAUNCHER="$HOME/.local/bin/claude-cc-teams"
if [ -r "$TEAMS_LAUNCHER" ]; then
  if ! grep -q "orca-lead-effort" "$TEAMS_LAUNCHER"; then
    warn "claude-cc-teams predates the lead effort pin; the effort config will be ignored"
    warn "rerun claude-env-stability/scripts/setup-claude-cc.sh --apply"
  fi
fi

# The guard now lives in project-level settings, so nothing about claude-cc
# needs to change. Remind the operator to install it per worktree instead.
log "[INFO] per-project guard: scripts/install-project-guard.sh <repo-path>"

# Content drift is reported but never blocks a deploy; the files on disk are
# already correct by this point and an urgent install should not be held up.
if [ -x "$CONSISTENCY" ]; then
  if ! "$CONSISTENCY" >/dev/null 2>&1; then
    warn "Skill content drifted from the canonical facts; run scripts/check-consistency.sh"
  fi
fi

if [ -d "$BACKUP_DIR" ]; then
  log "[INFO] backup: $BACKUP_DIR"
fi
done_msg "Orca task routing is installed. Restart Claude Code and Orca before using it."
