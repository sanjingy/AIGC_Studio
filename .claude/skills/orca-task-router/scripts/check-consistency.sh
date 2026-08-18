#!/usr/bin/env bash
# Input: none. Optional --live to also compare against locally installed CLIs.
# Output: drift findings between the canonical facts block and the rest of the Skill.
# Exit code: 0 when consistent; 1 when drift is found; 2 on a malformed facts block.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"
SSOT="$SKILL_DIR/references/session-launch-guide.md"

LIVE=0
[ "${1:-}" = "--live" ] && LIVE=1

[ -r "$SSOT" ] || { printf '[ERROR] missing SSOT: %s\n' "$SSOT" >&2; exit 2; }

FAILED=0
fail() { printf '[DRIFT] %s\n' "$1" >&2; FAILED=1; }
ok()   { printf '[OK] %s\n' "$1"; }

# ---------------------------------------------------------------- facts
facts_block() { awk '/^<!-- canonical-facts$/{f=1;next} /^-->$/{f=0} f' "$SSOT"; }
fact() {
  local key="$1" value
  value="$(facts_block | awk -F= -v k="$key" '$1==k{sub(/^[^=]*=/,"");print;exit}')"
  [ -n "$value" ] || { printf '[ERROR] fact not found: %s\n' "$key" >&2; exit 2; }
  printf '%s' "$value"
}

LEAD_MODEL="$(fact LEAD_MODEL)"
LEAD_EFFORT="$(fact LEAD_EFFORT)"
WORKER_MODEL="$(fact WORKER_MODEL)"
WORKER_EFFORT="$(fact WORKER_EFFORT)"
FABLE_MODEL="$(fact FABLE_MODEL)"
FABLE_DEFAULT_EFFORT="$(fact FABLE_DEFAULT_EFFORT)"
BACKUP_MODEL="$(fact BACKUP_MODEL)"
IMPL_BACKUP_EFFORT="$(fact IMPL_BACKUP_EFFORT)"
BACKUP_EFFORT="$(fact BACKUP_EFFORT)"
CODEX_MODEL="$(fact CODEX_MODEL)"
CODEX_EFFORT="$(fact CODEX_EFFORT)"
CODEX_MODEL_DAILY="$(fact CODEX_MODEL_DAILY)"
CODEX_EFFORT_DAILY="$(fact CODEX_EFFORT_DAILY)"
CODEX_MODEL_THRIFT="$(fact CODEX_MODEL_THRIFT)"
CODEX_EFFORT_THRIFT="$(fact CODEX_EFFORT_THRIFT)"
CODEX_CONTEXT_WINDOW="$(fact CODEX_CONTEXT_WINDOW)"
CODEX_TERRA_AT="$(fact CODEX_TERRA_AT)"
CODEX_LUNA_AT="$(fact CODEX_LUNA_AT)"
HANDOFF_THRESHOLD_TOKENS="$(fact HANDOFF_THRESHOLD_TOKENS)"
CLAUDE_CODE_VERSION="$(fact CLAUDE_CODE_VERSION)"
CODEX_CLI_VERSION="$(fact CODEX_CLI_VERSION)"
ORCA_VERSION="$(fact ORCA_VERSION)"

# Files that carry rules. Kept explicit so a new reference file is a conscious
# decision rather than something that silently escapes every check below.
RULE_FILES=(
  "$SKILL_DIR/SKILL.md"
  "$SKILL_DIR/references/session-launch-guide.md"
  "$SKILL_DIR/references/routing-policy.md"
  "$SKILL_DIR/references/orca-operations.md"
  "$SKILL_DIR/references/design-rationale.md"
  "$SKILL_DIR/assets/claude/rules/orca-task-routing.md"
  "$SKILL_DIR/assets/claude/orca-lead-system-prompt.txt"
  "$SKILL_DIR/evals/evals.json"
)

# Lines inside an allow block quote a deprecated claim on purpose, to explain
# why it is wrong. Everything else must not mention it at all.
exempt_lines() {
  case "$1" in
    *.json)
      # An eval set names the wrong behaviour on purpose so it can assert the
      # Lead avoids it, and JSON has no comment syntax to mark that with, so
      # exempt by structure. The skill-creator schema has one flat
      # expectations array, so the negative entries are marked by their leading
      # 没有 rather than by living in a separate must_not key. Only those are
      # exempt: a deprecated claim in a positive expectation is a real drift.
      # Parsed rather than pattern-matched: entries legitimately contain "[1m]",
      # and a bare /\]/ used to close the block one line early.
      python3 -c "
import json,sys
path=sys.argv[1]
src=open(path,encoding='utf-8').read()
try:
    doc=json.loads(src)
except Exception:
    raise SystemExit(0)
lines=src.split('\n')
for ev in doc.get('evals',[]):
    for item in ev.get('expectations',[]):
        if not item.startswith('没有'):
            continue
        for i,l in enumerate(lines,1):
            if json.dumps(item,ensure_ascii=False) in l:
                print(i)
" "$1"
      ;;
    *)
      awk '/<!-- consistency:allow-start -->/{a=1} {if(a)print NR} /<!-- consistency:allow-end -->/{a=0}' "$1"
      ;;
  esac
}

# ------------------------------------------------- 1. deprecated claims
check_banned() {
  local banned line file exempt hit
  banned="$(awk '/^BANNED=/{sub(/^BANNED=/,"");print}' "$SSOT")"
  [ -n "$banned" ] || { fail "no BANNED entries parsed from the SSOT"; return; }
  # File on the outside, banned entry on the inside -- the same nesting order as
  # check_model_flags below. exempt_lines only depends on the file, so hoisting it
  # out of the entry loop turns 13x8 calls into 8. Findings are now grouped by
  # file instead of by entry; the set of findings is unchanged.
  for file in "${RULE_FILES[@]}"; do
    [ -r "$file" ] || continue
    exempt="$(exempt_lines "$file")"
    while IFS= read -r line; do
      [ -n "$line" ] || continue
      # The facts block itself defines these strings; never flag it.
      while IFS=: read -r num text; do
        [ -n "$num" ] || continue
        case "$text" in BANNED=*|*canonical-facts*) continue ;; esac
        if printf '%s\n' "$exempt" | grep -qx "$num"; then continue; fi
        hit="${file#$SKILL_DIR/}:$num"
        fail "deprecated claim \"$line\" at $hit"
      # `--` is mandatory: BANNED entries such as "--max-concurrent" are
      # otherwise parsed as grep options. grep then errors out, the error goes
      # to /dev/null, `|| true` swallows the status, and the loop body never
      # runs. That entry was dead from the moment it was added.
      done < <(grep -n -F -- "$line" "$file" 2>/dev/null || true)
    done <<< "$banned"
  done
}

# --------------------------------- 2. --model arguments must pin context
check_model_flags() {
  local file num text model exempt
  for file in "${RULE_FILES[@]}"; do
    [ -r "$file" ] || continue
    exempt="$(exempt_lines "$file")"
    while IFS=: read -r num text; do
      [ -n "$num" ] || continue
      if printf '%s\n' "$exempt" | grep -qx "$num"; then continue; fi
      # Extract every model token after --model on this line. Done in python
      # because the shell version stopped at the first backslash: the nested
      # form that `orca terminal create --command` requires is written
      # --model \'\\\'\'claude-...\'\\\'\' , so every real launch command in the
      # handbook slipped past the check while the fixture, which used the flat
      # form, kept reporting the check as working.
      while read -r model; do
        [ -n "$model" ] || continue
        case "$model" in
          *'[1m]') ;;
          fable|opus|sonnet|haiku)
            fail "bare alias --model $model at ${file#$SKILL_DIR/}:$num; aliases drift across CLI versions"
            ;;
          claude-*)
            fail "--model $model at ${file#$SKILL_DIR/}:$num lacks [1m]; Claude Code caps it at 200k"
            ;;
        esac
      done < <(printf '%s\n' "$text" | python3 -c "
import re,sys
pat=re.compile(r\"--model[= ][\\x27\\\\\\\"]*([A-Za-z0-9._-]+(?:\\[1m\\])?)\")
for line in sys.stdin:
    for m in pat.finditer(line):
        print(m.group(1))
")
    done < <(grep -n -- "--model" "$file" 2>/dev/null || true)
  done
}

# --------------------- 2b. claude launch commands must pair model and effort
# Every (model, effort) pair the Skill may launch is enumerated from the facts.
# A pair outside the table is how the routing intent quietly inverts: a worker
# copying the lead's max, or the 4.8 implementation tier sliding off xhigh.
# Both slides still run fine, which is exactly why nothing else would notice.
check_claude_efforts() {
  local file num text model effort exempt cmd
  for file in "${RULE_FILES[@]}"; do
    [ -r "$file" ] || continue
    exempt="$(exempt_lines "$file")"
    while IFS=: read -r num text; do
      [ -n "$num" ] || continue
      if printf '%s\n' "$exempt" | grep -qx "$num"; then continue; fi
      while read -r model effort; do
        [ -n "$model" ] && [ -n "$effort" ] || continue
        case "$model:$effort" in
          "$LEAD_MODEL:$LEAD_EFFORT"|"$WORKER_MODEL:$WORKER_EFFORT") ;;
          "$BACKUP_MODEL:$IMPL_BACKUP_EFFORT"|"$BACKUP_MODEL:$BACKUP_EFFORT") ;;
          # Fable's upgrade tiers are gated by prose, not by the facts block, so
          # all three of its efforts are launchable on purpose.
          "$FABLE_MODEL:$FABLE_DEFAULT_EFFORT"|"$FABLE_MODEL:xhigh"|"$FABLE_MODEL:max") ;;
          *)
            fail "claude launch at ${file#$SKILL_DIR/}:$num pairs $model with effort '$effort', which no tier in the facts allows"
            ;;
        esac
      done < <(printf '%s\n' "$text" | python3 -c "
import re,sys
mpat=re.compile(r\"--model[= ][\\x27\\\\\\\"]*([A-Za-z0-9._-]+(?:\\[1m\\])?)\")
epat=re.compile(r\"--effort[= ]+([a-z]+)\")
for line in sys.stdin:
    m=mpat.search(line); e=epat.search(line)
    if m and e and m.group(1).startswith('claude-'):
        print(m.group(1), e.group(1))
")
    done < <(grep -nE -- "--model.*--effort|--effort.*--model" "$file" 2>/dev/null || true)
  done

  # The two implementation launch commands must survive verbatim in the SSOT.
  # The pair table above cannot see an effort flip that lands on another legal
  # pair (worker high -> lead max is still a legal pair), but the flip deletes
  # the command this check pins, so it fails here instead.
  for cmd in \
    "claude --model 'claude-opus-5[1m]' --effort $WORKER_EFFORT --dangerously-skip-permissions" \
    "claude --model 'claude-opus-4-8[1m]' --effort $IMPL_BACKUP_EFFORT --dangerously-skip-permissions"; do
    grep -qF -- "$cmd" "$SSOT" \
      && ok "SSOT carries the launch command: $cmd" \
      || fail "SSOT lost the implementation launch command: $cmd"
  done
}

# ------------------------------- 3. agent definitions must match the facts
check_agent_defs() {
  local file model tools tool bad effort want_effort
  for file in "$SKILL_DIR"/assets/claude/agents/*.md; do
    [ -r "$file" ] || continue
    model="$(awk -F': *' '/^model:/{print $2;exit}' "$file")"
    case "$model" in
      "$LEAD_MODEL"|"$FABLE_MODEL"|"$BACKUP_MODEL")
        ok "agent ${file##*/} pins $model"
        ;;
      *)
        fail "agent ${file##*/} uses model '$model', which is not in the canonical facts"
        ;;
    esac

    # max belongs to the lead alone; every dispatched Claude teammate runs the
    # worker tier. The reasons are throughput and overthinking, not quota, so a
    # teammate quietly copying the lead's effort is drift even though it works.
    effort="$(awk -F': *' '/^effort:/{print $2;exit}' "$file")"
    if [ "$model" = "$FABLE_MODEL" ]; then
      want_effort="$FABLE_DEFAULT_EFFORT"
    else
      want_effort="$WORKER_EFFORT"
    fi
    [ "$effort" = "$want_effort" ] \
      && ok "agent ${file##*/} runs effort $effort" \
      || fail "agent ${file##*/} declares effort '$effort' but teammates run '$want_effort'; max belongs to the lead alone"

    # tools is the only field that actually constrains a teammate. permissionMode
    # does not carry the read-only guarantee: teammates inherit the lead's mode at
    # spawn time, and every launch command in this Skill passes bypass flags, under
    # which plan mode stops blocking anything. So assert the capability set itself.
    #
    # Whitelist rather than blacklist, and match whole tokens: a *Bash* substring
    # test also accepts BashOutput, and an *Edit* test hides NotebookEdit. A new
    # tool therefore has to be added here on purpose instead of arriving silently.
    tools="$(awk -F': *' '/^tools:/{print $2;exit}' "$file")"
    if [ -z "$tools" ]; then
      fail "agent ${file##*/} declares no tools field, so its capabilities are whatever the parent grants"
      continue
    fi
    bad=""
    for tool in $(printf '%s' "$tools" | tr ',' ' '); do
      case "$tool" in
        Read|Grep|Glob|Bash) ;;
        *) bad="$bad $tool" ;;
      esac
    done
    if [ -n "$bad" ]; then
      fail "agent ${file##*/} grants disallowed tool(s):$bad. Teammates may only hold Read, Grep, Glob and Bash; anything that writes belongs to a Terminal Worker"
    else
      ok "agent ${file##*/} grants only read and execute tools"
    fi
  done
}

# --------------------------- 4. installed lead config must match the facts
check_lead_assets() {
  local model_file="$SKILL_DIR/assets/claude/orca-lead-model"
  local effort_file="$SKILL_DIR/assets/claude/orca-lead-effort"
  local got
  got="$(tr -d '\r\n' < "$model_file" 2>/dev/null || true)"
  [ "$got" = "$LEAD_MODEL" ] \
    && ok "orca-lead-model matches LEAD_MODEL ($LEAD_MODEL)" \
    || fail "orca-lead-model is '$got' but LEAD_MODEL is '$LEAD_MODEL'"
  got="$(tr -d '\r\n' < "$effort_file" 2>/dev/null || true)"
  [ "$got" = "$LEAD_EFFORT" ] \
    && ok "orca-lead-effort matches LEAD_EFFORT ($LEAD_EFFORT)" \
    || fail "orca-lead-effort is '$got' but LEAD_EFFORT is '$LEAD_EFFORT'"
}

# -------------------- 4b. context guard default must match the facts
check_guard_threshold() {
  local guard="$SKILL_DIR/assets/claude/bin/orca-context-guard.py"
  local got
  if [ ! -r "$guard" ]; then
    fail "context guard script is missing: assets/claude/bin/orca-context-guard.py"
    return
  fi
  got="$(awk -F'=' '/^DEFAULT_THRESHOLD/{gsub(/[^0-9]/,"",$2);print $2;exit}' "$guard")"
  [ "$got" = "$HANDOFF_THRESHOLD_TOKENS" ] \
    && ok "context guard default matches HANDOFF_THRESHOLD_TOKENS ($HANDOFF_THRESHOLD_TOKENS)" \
    || fail "context guard default is '$got' but HANDOFF_THRESHOLD_TOKENS is '$HANDOFF_THRESHOLD_TOKENS'"

  # The guard is wired from project-level settings. The original reason -- that
  # claude-cc's --settings shadows user-level hooks -- was disproved on
  # 2026-08-05 (Claude Code 2.1.222): user-level hooks do fire, --settings
  # merges. The project-level installer is still what wires this particular
  # guard, so it stays the thing to assert. So the project-level
  # installer is what actually wires the guard into a Worker. An older
  # ~/.config overlay used to be checked here; it never had a consumer after
  # the claude-cc merge was reverted, and checking a dead file is how a green
  # run stops meaning anything.
  local installer="$SKILL_DIR/scripts/install-project-guard.sh"
  if [ ! -x "$installer" ]; then
    fail "scripts/install-project-guard.sh is missing or not executable"
  else
    grep -q "orca-context-guard" "$installer" \
      || fail "install-project-guard.sh no longer references the context guard"
    # The guard reads its payload from the event it is registered on; a
    # mismatched hookEventName discards the payload silently.
    grep -q '"PostToolUse"' "$installer" \
      || fail "install-project-guard.sh does not register the guard on PostToolUse"
  fi
  if ! grep -q "install-project-guard.sh" "$SKILL_DIR/references/orca-operations.md"; then
    fail "orca-operations.md does not tell the Lead to install the project-level guard"
  fi

  # Behavioural claims in the handbook are only as good as the run that proved
  # them. The smoke test is that run; losing it silently is how the handbook
  # drifts back into being written from help text.
  local smoke="$SKILL_DIR/scripts/smoke-orchestration.sh"
  if [ ! -x "$smoke" ]; then
    fail "scripts/smoke-orchestration.sh is missing or not executable"
  elif ! grep -q "smoke-orchestration.sh" "$SKILL_DIR/SKILL.md"; then
    fail "SKILL.md does not tell maintainers to run the behavioural smoke test"
  else
    # `close --tab` leaves the pty running, so the reclaim path must not use it.
    # Scope the check to close_terminal(): the harness also calls --tab on
    # purpose, in the assertion that pins this very behaviour.
    if awk '/^close_terminal\(\) \{/,/^\}/' "$smoke" | grep -q -- "--tab"; then
      fail "close_terminal() in smoke-orchestration.sh uses --tab, which does not kill the pty"
    fi
    grep -q "ptyKilled" "$smoke" \
      || fail "smoke-orchestration.sh no longer verifies ptyKilled when closing terminals"
    # check_codex_command below only scans RULE_FILES, so the harness was the one
    # place Fast could sit unchallenged -- and it did, for two versions after the
    # ban, launching a tier the handbook forbids while claiming to verify the
    # tier the handbook prescribes.
    # Matched loosely on purpose: inside the harness the flag is written
    # service_tier=\"fast\", so the exact literal the doc-level check greps for
    # does not appear here at all.
    if grep -qE 'service_tier=[^ ]*fast' "$smoke"; then
      fail "smoke-orchestration.sh launches a Codex tier with Fast enabled, which every documented tier forbids"
    fi
  fi

  # The guard's percentage path depends on window facts published by the
  # statusline; hooks get no window size of their own. If the two drift apart
  # the guard silently falls back to a fixed token count that a 200k session
  # can never reach.
  local statusline="$SKILL_DIR/assets/claude/statusline-command.sh"
  if [ ! -r "$statusline" ]; then
    fail "assets/claude/statusline-command.sh is missing; the guard loses its window source"
  else
    grep -q "claude-ctx-" "$statusline" \
      || fail "statusline no longer publishes context facts the guard reads"
    grep -q "claude-ctx-" "$guard" \
      || fail "context guard no longer reads the statusline context facts"
  fi

  # Every Claude launch command anywhere in the Skill must enable the guard.
  # Scanning only the operations handbook let commands in the authoritative
  # guide drift silently, and anchoring on `--command` let a one-line rewrite
  # skip the check entirely. Match the launch itself instead.
  local file num text exempt
  for file in "${RULE_FILES[@]}"; do
    [ -r "$file" ] || continue
    exempt="$(exempt_lines "$file")"
    while IFS=: read -r num text; do
      [ -n "$num" ] || continue
      if printf '%s\n' "$exempt" | grep -qx "$num"; then continue; fi
      case "$text" in *"|"*) continue ;; esac   # prose table, not a command
      case "$text" in ">"*) continue ;; esac    # quoted block
      # Use if/then, not `grep && fail`: a non-matching grep returns 1 and
      # would abort the whole script under `set -e`.
      if printf '%s' "$text" | grep -q -- "--settings"; then
        fail "Claude launch at ${file#$SKILL_DIR/}:$num passes --settings, which claude-cc rejects"
      fi
    # Anchor on the model argument, not on `claude --model` as a contiguous
    # string: a command may legitimately carry other flags in between, and
    # anchoring on the shape let `claude --settings x --model y` slip past.
    # Anchor on the model argument. The nested form inside `orca terminal
    # create --command` writes it as --model '\''claude-...'\'', so allow any
    # run of quotes and backslashes between the flag and the model name.
    done < <(grep -nE -- "--model[= ][^A-Za-z]*claude-" "$file" 2>/dev/null || true)
  done
}

# -------------------- 4c. the model guard must stay wired end to end
# Three things have to line up or the guard is decorative: the script exists,
# the installer copies it AND registers it, and the registration names the
# event and matcher the payload actually arrives on. A hook on the wrong event
# gets no tool_input and denies nothing, silently.
check_model_guard() {
  local guard="$SKILL_DIR/assets/claude/bin/orca-model-guard.py"
  local installer="$SKILL_DIR/scripts/install.sh"

  if [ ! -r "$guard" ]; then
    fail "model guard script is missing: assets/claude/bin/orca-model-guard.py"
  else
    grep -q '"permissionDecision"' "$guard" \
      && grep -q '"deny"' "$guard" \
      && ok "model guard emits a deny decision" \
      || fail "model guard no longer emits permissionDecision deny"
    # The whole point is that no alias is acceptable. An allow-list creeping
    # back in would re-open exactly the hole this guard was written to close.
    # BSD grep's ERE has no \s, so a class that actually exists is used here;
    # \s matched nothing and the check passed on an indented allow-list.
    grep -qE '^[[:space:]]*(ALLOWED|ALLOW_LIST|WHITELIST)[[:space:]]*=' "$guard" \
      && fail "model guard grew a model allow-list; the rule is that no value is legal" \
      || ok "model guard keeps the no-legal-value rule"
    # The matcher is a regex; without a tool_name check the guard denies any
    # model-bearing call from a tool whose name merely contains "Agent".
    grep -q 'tool_name' "$guard" \
      && ok "model guard checks tool_name" \
      || fail "model guard no longer checks tool_name"
    # The remediation text names the tier a denied caller should fall back to.
    # If LEAD_MODEL moves and the guard keeps naming the old one, every denial
    # points the caller at a model the rest of the Skill no longer uses.
    # -F is required, not stylistic: LEAD_MODEL ends in [1m], which a BRE reads
    # as a character class, so a plain grep matches "claude-opus-5m" and misses
    # the literal the guard actually contains.
    grep -qF -- "$LEAD_MODEL" "$guard" \
      && ok "model guard remediation names LEAD_MODEL ($LEAD_MODEL)" \
      || fail "model guard's remediation text does not name LEAD_MODEL ($LEAD_MODEL)"
  fi

  if [ ! -r "$installer" ]; then
    fail "scripts/install.sh is missing"
  else
    grep -q "orca-model-guard" "$installer" \
      || fail "install.sh no longer references the model guard"
    grep -q '"PreToolUse"' "$installer" \
      || fail "install.sh does not register the model guard on PreToolUse"
    grep -q '"matcher": "Agent"' "$installer" \
      || fail "install.sh does not register the model guard on the Agent matcher"
  fi

  grep -q "orca-model-guard" "$SKILL_DIR/SKILL.md" \
    || fail "SKILL.md does not tell the Lead the model guard exists"
}

# ------------------------------- 4d. the eval set must not go stale
# evals.json had no runner and nothing referencing it, so it silently kept
# asserting a dispatch flow the handbook had already replaced. An eval set that
# contradicts the rules is worse than none: it certifies the wrong behaviour.
check_evals() {
  local evals="$SKILL_DIR/evals/evals.json" skill_version eval_version
  if [ ! -r "$evals" ]; then
    fail "evals/evals.json is missing"
    return
  fi
  if ! python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$evals" 2>/dev/null; then
    fail "evals/evals.json is not valid JSON"
    return
  fi
  skill_version="$(awk -F': *' '/^version:/{print $2;exit}' "$SKILL_DIR/SKILL.md")"
  eval_version="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('version',''))" "$evals")"
  [ "$skill_version" = "$eval_version" ] \
    && ok "evals.json tracks SKILL.md version ($skill_version)" \
    || fail "evals.json says version '$eval_version' but SKILL.md says '$skill_version'"
  # The eval set is consumed by skill-creator's with_skill/without_skill loop,
  # which reads these field names exactly. A file that drifts back to ad-hoc
  # keys stops being runnable without anything visibly breaking.
  python3 -c "
import json,sys
d=json.load(open(sys.argv[1]))
if d.get('skill_name') != 'orca-task-router':
    sys.exit(1)
evs=d.get('evals') or []
if not evs:
    sys.exit(1)
seen=set()
for e in evs:
    if not isinstance(e.get('id'), int) or e['id'] in seen:
        sys.exit(1)
    seen.add(e['id'])
    if not (e.get('prompt') and e.get('expected_output') and e.get('expectations')):
        sys.exit(1)
    if not isinstance(e.get('files'), list):
        sys.exit(1)
sys.exit(0)" "$evals" 2>/dev/null \
    || fail "evals.json does not match the skill-creator schema (skill_name + evals[].{id,prompt,expected_output,files,expectations})"
}

# ------------------- 4e. the Lead must be able to reclaim worker terminals
# A missing grant does not fail loudly -- it stops mid-run for a permission
# prompt, halfway through a state machine the Lead cannot restart. `show` is
# here because the handbook tells the Lead to distinguish a leftover shell from
# a live worker before closing anything, and closing the wrong one kills work.
check_reclaim_grant() {
  local skill="$SKILL_DIR/SKILL.md" tool
  [ -r "$skill" ] || return
  for tool in "orca terminal close" "orca terminal read" "orca terminal list" "orca terminal show"; do
    if ! grep -q "Bash($tool:\*)" "$skill"; then
      fail "SKILL.md allowed-tools is missing Bash($tool:*); the Lead cannot reclaim worker terminals without prompting"
    fi
  done
}

# ------------- 4f. every script the handbook runs at dispatch time needs a grant
# Maintenance scripts are run by a human and need nothing. This one is different:
# the handbook has the Lead execute it while setting up a Worker, so an absent
# grant surfaces as a prompt in the middle of dispatch.
check_runtime_script_grant() {
  local skill="$SKILL_DIR/SKILL.md" handbook="$SKILL_DIR/references/orca-operations.md"
  [ -r "$skill" ] && [ -r "$handbook" ] || return
  grep -q "install-project-guard.sh" "$handbook" || return
  grep -q "Bash(.*install-project-guard\.sh:\*)" "$skill" \
    && ok "the guard installer the handbook runs is granted in allowed-tools" \
    || fail "references/orca-operations.md runs install-project-guard.sh but SKILL.md grants no Bash permission for it; dispatch will stall on a prompt"
}

# ------------- 4e. worker terminals must be pinned to a worktree
# Two failure modes, both bad in different ways. `terminal create` without
# --worktree lands in whatever Orca considers active, which need not be where
# the Lead is; the Worker then edits a checkout nobody is watching and nothing
# errors until a diff shows up in the wrong repo. `worker-start` without it
# resolves the handle against the Lead's own worktree and rejects with
# terminal_worktree_mismatch -- loud, but only after dispatch is half done.
# The rule itself lives in SKILL.md step 4. Assert it is still written down:
# before v0.13.0 it existed only as an implication of the recipes below, which
# means deleting one line of prose would have silently removed it.
check_worktree_pinning() {
  local skill="$SKILL_DIR/SKILL.md" file num
  [ -r "$skill" ] || return
  if grep -q "默认起在Lead" "$skill"; then
    ok "SKILL.md states that a Worker terminal defaults to the Lead's worktree"
  else
    fail "SKILL.md no longer states that a Worker terminal defaults to the Lead's worktree; the rule would survive only as an implication of the handbook recipes"
  fi
  for file in "${RULE_FILES[@]}"; do
    case "$file" in *.md) ;; *) continue ;; esac
    [ -r "$file" ] || continue
    # Continuation lines are joined before the check: every recipe in the
    # handbook puts --worktree on its own line, so a naive per-line grep would
    # report all three as violations.
    while IFS= read -r num; do
      [ -n "$num" ] || continue
      fail "${file#$SKILL_DIR/}:$num creates a terminal without --worktree; it lands in whatever worktree Orca considers active, not necessarily the Lead's"
    done < <(python3 - "$file" <<'PY'
import sys

path = sys.argv[1]
inblock = False
pending = None
found = []

# Spelled this way on purpose: bash parses backticks inside a process
# substitution even when the heredoc delimiter is quoted, so a literal fence
# here breaks the enclosing script with "unexpected EOF".
FENCE = chr(96) * 3

for lineno, line in enumerate(open(path, encoding="utf-8").read().split("\n"), 1):
    if line.lstrip().startswith(FENCE):
        if pending:
            found.append(pending)
            pending = None
        inblock = not inblock
        continue
    if not inblock:
        continue
    text = line.strip()
    if pending is not None:
        pending = (pending[0], pending[1] + " " + text)
        if not text.endswith("\\"):
            found.append(pending)
            pending = None
        continue
    if text.startswith("#") or "orca terminal create" not in text:
        continue
    pending = (lineno, text)
    if not text.endswith("\\"):
        found.append(pending)
        pending = None

if pending:
    found.append(pending)

for lineno, cmd in found:
    if "--worktree" not in cmd:
        print(lineno)
PY
    )
  done
}

# ------------------------------------- 5. codex command must stay complete
# Each Codex launch line must match one of the three tiers exactly. Asserting
# "some valid effort appears" would accept a Luna command carrying ultra, which
# the model does not even support, so the model picks the effort here.
#
# Fast is now asserted absent rather than present. It is a pure speed-for-quota
# trade (the model catalog calls it "1.5x speed, increased usage") and it
# multiplies the whole bill, so it is off in every tier.
check_codex_command() {
  local file num text want_effort
  for file in "${RULE_FILES[@]}"; do
    [ -r "$file" ] || continue
    while IFS=: read -r num text; do
      [ -n "$num" ] || continue
      case "$text" in *"model_reasoning_effort"*) ;; *) continue ;; esac

      # Longest name first: gpt-5.6-sol is not a substring of the others, but
      # matching in declaration order keeps this correct if a name ever nests.
      want_effort=""
      case "$text" in
        *"$CODEX_MODEL_THRIFT"*) want_effort="$CODEX_EFFORT_THRIFT" ;;
        *"$CODEX_MODEL_DAILY"*)  want_effort="$CODEX_EFFORT_DAILY" ;;
        *"$CODEX_MODEL"*)        want_effort="$CODEX_EFFORT" ;;
      esac
      if [ -z "$want_effort" ]; then
        fail "codex command at ${file#$SKILL_DIR/}:$num names no tier model from the facts"
        continue
      fi

      printf '%s' "$text" | grep -q "\"$want_effort\"" \
        || fail "codex command at ${file#$SKILL_DIR/}:$num does not use effort $want_effort for its model"
      printf '%s' "$text" | grep -q "model_context_window=$CODEX_CONTEXT_WINDOW" \
        || fail "codex command at ${file#$SKILL_DIR/}:$num does not set context $CODEX_CONTEXT_WINDOW"
      printf '%s' "$text" | grep -q 'service_tier="fast"' \
        && fail "codex command at ${file#$SKILL_DIR/}:$num still enables Fast" \
        || true
    done < <(grep -nE -- "$CODEX_MODEL|$CODEX_MODEL_DAILY|$CODEX_MODEL_THRIFT" "$file" 2>/dev/null || true)
  done

  # The probe is what makes the tier table actionable; without it the Lead has
  # no way to read the level the thresholds are written against.
  local probe="$SKILL_DIR/assets/claude/bin/orca-codex-usage.py"
  if [ ! -r "$probe" ]; then
    fail "codex usage probe is missing: assets/claude/bin/orca-codex-usage.py"
  else
    grep -q "used_percent" "$probe" \
      || fail "codex usage probe no longer reads used_percent"
    # Ask the script rather than grep its source: the literal "60" also appears
    # inside the 3600 seconds-to-hours divisor, so a text match stayed green on
    # a probe whose threshold had actually been changed. -u clears the operator
    # overrides so this compares the built-in defaults to the facts.
    local got
    got="$(env -u ORCA_CODEX_TERRA_AT -u ORCA_CODEX_LUNA_AT \
      python3 "$probe" --thresholds 2>/dev/null || true)"
    [ "$got" = "$CODEX_TERRA_AT $CODEX_LUNA_AT" ] \
      && ok "codex usage probe thresholds match the facts ($CODEX_TERRA_AT/$CODEX_LUNA_AT)" \
      || fail "codex usage probe reports thresholds '$got' but facts say '$CODEX_TERRA_AT $CODEX_LUNA_AT'"
  fi
}

# ------------------------------------------ 6. version numbers agree
check_versions() {
  local file pair name want
  for pair in "Claude Code:$CLAUDE_CODE_VERSION" "Codex CLI:$CODEX_CLI_VERSION" "Orca:$ORCA_VERSION"; do
    name="${pair%%:*}"; want="${pair##*:}"
    for file in "${RULE_FILES[@]}"; do
      [ -r "$file" ] || continue
      while IFS=: read -r num text; do
        [ -n "$num" ] || continue
        case "$text" in *"$want"*) continue ;; esac
        fail "$name version on ${file#$SKILL_DIR/}:$num disagrees with facts ($want)"
      done < <(grep -n -E "$name.*\`[0-9]+\.[0-9]+\.[0-9]+" "$file" 2>/dev/null || true)
    done
  done
  # Any three-part version that looks like a stale copy of a tracked one.
  local stray
  stray="$(grep -rn -oE "1\.4\.1[0-9]{2}|0\.1[0-9]{2}\.[0-9]+|2\.1\.[0-9]{3}" "${RULE_FILES[@]}" 2>/dev/null \
    | grep -vE ":($CLAUDE_CODE_VERSION|$CODEX_CLI_VERSION|$ORCA_VERSION)\$" || true)"
  # A superseded version number is sometimes the point rather than a leftover:
  # "alias X resolved to A on 2.1.217 and to B on 2.1.222" is the evidence for
  # not trusting aliases at all, and rewriting it without the old number
  # destroys the argument. Honour the same allow block the other checks use
  # instead of pushing the fact out of the docs.
  local sfile snum cached_file="" cached=""
  [ -z "$stray" ] || while IFS= read -r line; do
    [ -n "$line" ] || continue
    sfile="${line%%:*}"
    snum="${line#*:}"; snum="${snum%%:*}"
    if [ "$sfile" != "$cached_file" ]; then
      cached="$(exempt_lines "$sfile")"
      cached_file="$sfile"
    fi
    if printf '%s\n' "$cached" | grep -qx "$snum"; then continue; fi
    fail "stale version literal ${line#$SKILL_DIR/}"
  done <<< "$stray"
}

# --------------------------------------- 7. optional: compare to this machine
check_live() {
  local got
  if command -v claude >/dev/null 2>&1; then
    got="$(claude --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
    [ "$got" = "$CLAUDE_CODE_VERSION" ] \
      && ok "live Claude Code $got matches facts" \
      || fail "live Claude Code is $got but facts say $CLAUDE_CODE_VERSION"
  fi
  if command -v codex >/dev/null 2>&1; then
    got="$(codex --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
    [ "$got" = "$CODEX_CLI_VERSION" ] \
      && ok "live Codex CLI $got matches facts" \
      || fail "live Codex CLI is $got but facts say $CODEX_CLI_VERSION"
  fi
  # Orca was the one tracked version nothing compared against, so the facts sat
  # five patch releases behind while every grep reported green. `orca --version`
  # does not exist; the runtime reports appVersion instead, which also requires
  # the desktop app to be running.
  if command -v orca >/dev/null 2>&1; then
    got="$(orca status --json 2>/dev/null \
      | grep -o '"appVersion"[^,}]*' | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
    if [ -z "$got" ]; then
      ok "orca runtime not reachable; skipped the Orca version comparison"
    else
      [ "$got" = "$ORCA_VERSION" ] \
        && ok "live Orca $got matches facts" \
        || fail "live Orca is $got but facts say $ORCA_VERSION"
    fi
  fi
}

# ------------- 8. optional: official commands the handbook depends on
# The protocol layer deliberately lives in Orca's bundled skill rather than in
# this repo, so none of the content checks above can see it. Probe every
# official subcommand the handbook tells the Lead to run. `--help` has no side
# effects, but exit status alone is not enough: a retired command still exits 0
# and prints a "Retired:" banner. `orchestration run` survived in the handbook
# for months precisely because nothing looked.
OFFICIAL_COMMANDS=(
  "status"
  "skills get"
  "orchestration run-create"
  "orchestration run-use"
  "orchestration run-current"
  "orchestration run-list"
  "orchestration task-create"
  "orchestration task-list"
  "orchestration task-update"
  "orchestration dispatch"
  "orchestration dispatch-show"
  "orchestration check"
  "orchestration send"
  "orchestration reply"
  "orchestration worker-start"
  "orchestration worker-show"
  "orchestration worker-stop"
  "orchestration worker-abandon"
  "orchestration worker-release"
  "terminal create"
  "terminal wait"
  "terminal read"
  "terminal send"
  "terminal close"
  "terminal stop"
  "terminal list"
  "worktree list"
  "worktree create"
  "repo list"
)

check_official_commands() {
  if ! command -v orca >/dev/null 2>&1; then
    ok "orca is not installed; skipped the official command probe"
    return
  fi
  local cmd out live=0
  for cmd in "${OFFICIAL_COMMANDS[@]}"; do
    # Word splitting on $cmd is intentional: these are multi-word subcommands.
    # shellcheck disable=SC2086
    if ! out="$(orca $cmd --help 2>&1)"; then
      fail "official command 'orca $cmd' no longer exists, but the Skill still tells the Lead to run it"
      continue
    fi
    case "$out" in
      *"Retired:"*)
        fail "official command 'orca $cmd' is retired; stop teaching it and reread 'orca skills get orchestration'"
        continue
        ;;
    esac
    live=$((live + 1))
  done
  ok "$live official commands the Skill depends on are live and not retired"
}

printf '# Orca Task Router Consistency Check\n'
check_banned
check_model_flags
check_claude_efforts
check_agent_defs
check_lead_assets
check_guard_threshold
check_model_guard
check_evals
check_reclaim_grant
check_runtime_script_grant
check_worktree_pinning
check_codex_command
check_versions
if [ "$LIVE" -eq 1 ]; then
  check_live
  check_official_commands
fi

if [ "$FAILED" -eq 0 ]; then
  printf '[DONE] canonical facts and the rest of the Skill agree\n'
  exit 0
fi
printf '[FAIL] fix the drift above, or update the canonical-facts block if the change is intentional\n' >&2
exit 1
