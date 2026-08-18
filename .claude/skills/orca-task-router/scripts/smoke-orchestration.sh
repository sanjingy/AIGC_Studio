#!/usr/bin/env bash
# Input: none. Optional --keep to leave the probe worktree behind for inspection.
# Output: pass/fail for every behavioural claim the handbook makes about Orca.
# Exit code: 0 when every assertion holds; 1 if any claim no longer matches.
#
# Why this exists: check-consistency.sh proves the handbook spells its commands
# correctly. It cannot prove those commands do what the handbook says. Every
# error this Skill has shipped was the second kind, written from `--help` and
# never run: --retry-of silently refused, a reopened task redelivering its
# original spec, worker-start rejecting a cross-worktree terminal, worker-stop
# quietly doing three things the handbook credited to two commands.
#
# Determinism note. An earlier version drove the state machine with a real LLM
# worker and was unusable: whether an assertion passed depended on whether the
# model chose to obey its task spec, so the same commit passed and failed on
# consecutive runs. `worker_done` authority is bound to the dispatch's assignee
# terminal rather than to the ids on the command line, and a plain `sh` terminal
# can be an assignee via `orchestration dispatch`. So the lifecycle assertions
# here are driven by sending shell commands into a shell, with no model in the
# loop. Only two things still need a real agent, and neither depends on what the
# agent decides to do: the supervised commands refuse anything that is not a
# recognised agent, and the routing tiers are read off a session banner or a
# headless probe without dispatching any work.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"
SSOT="$SKILL_DIR/references/session-launch-guide.md"

# Cheap agent for the assertions that require a recognised agent but never ask
# it to think. Not a routing tier.
PROBE_AGENT="claude --model claude-haiku-4-5-20251001 --dangerously-skip-permissions"

KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1

FAILED=0
pass() { printf '[PASS] %s\n' "$1"; }
fail() { printf '[FAIL] %s\n' "$1" >&2; FAILED=1; }
info() { printf '[INFO] %s\n' "$1"; }

# Orca exits non-zero on refusals this script provokes on purpose, and every
# reader below inspects the JSON rather than $?. Under `set -e` an expected
# refusal would otherwise kill the run before the assertion that wanted it.
oc() { orca "$@" 2>/dev/null || true; }

# Same call, but the exit status survives. `oc` deliberately returns 0 always,
# which quietly turned every `oc ... && pass || fail` into an unconditional
# PASS. Anything that judges success by exit status must use this instead --
# or better, judge by the payload, which is what most assertions below do.
ocx() { orca "$@" 2>/dev/null; }

# `orca --json` emits pretty-printed multi-line JSON, not JSON Lines. Parsing it
# line by line silently yields nothing, which looks exactly like a command that
# produced no output.
# Prints an empty line rather than raising when the input is not JSON. Under
# `set -e` an unhandled traceback inside a command substitution kills the run
# with zero [FAIL] lines printed -- the mirror image of the bug where failures
# printed but the exit code stayed 0. Callers already treat "" as absent.
jget() { python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
except Exception:
    print(''); raise SystemExit(0)
cur=d
for k in sys.argv[1].split('.'):
    if cur is None: break
    cur=cur.get(k) if isinstance(cur,dict) else None
print('' if cur is None else cur)
" "$1"; }

# Takes the payload as an argument, never on stdin. Reading it from a pipe puts
# this function in a subshell, where `fail` sets FAILED=1 in a copy of the
# environment that dies with the pipe: the run then printed [FAIL] lines and
# still exited 0. A test harness that reports failures and passes anyway is
# worse than no harness, so the payload is passed by value on purpose.
expect_error() {
  local label="$1" want="$2" got="$3" ok code
  ok="$(printf '%s' "$got" | jget ok)"
  code="$(printf '%s' "$got" | jget error.code)"
  if [ "$ok" = "True" ]; then
    fail "$label: expected '$want' but the call succeeded"
  elif [ "$code" = "$want" ]; then
    pass "$label rejected with $code"
  else
    fail "$label: expected '$want' but got '$code'"
  fi
}

# Aborts on a missing key rather than returning empty, matching
# check-consistency.sh. The silent-empty version was worse than useless here:
# an empty value flows into `grep -qF ""`, which matches anything, so renaming a
# key turned the tier assertions into unconditional passes while the launch
# command silently degraded to `claude --model '' --effort`.
fact() {
  local v
  v="$(awk -v k="$1" '/^<!-- canonical-facts$/{f=1;next} /^-->$/{f=0} f && index($0,k"=")==1 {sub(/^[^=]*=/,"");print;exit}' "$SSOT")"
  if [ -z "$v" ]; then
    printf '[FAIL] canonical fact not found: %s\n' "$1" >&2
    exit 2
  fi
  printf '%s' "$v"
}

TASK_IDS=""
TERMINALS=""
WORKTREE_ID=""
PRIOR_RUN=""
BASELINE_TERMINALS=""
BASELINE_OK=""

# Orca has no command that deletes a Run and `orchestration reset` would take
# unrelated Runs with it, so this harness must never create one per invocation.
# It claims any Run it previously made. An earlier version created one each time
# and left four behind in a single afternoon, none of them removable.
SMOKE_OBJECTIVE="orca-task-router smoke harness (reused across runs)"

# `close --tab` returns ok while leaving the pty alive (ptyKilled:false); only
# the plain close actually kills it. An earlier version of this harness used
# --tab everywhere and left a dozen live workers behind, one of which was still
# posting worker_done into the Run long after the run finished. Verify by
# ptyKilled, then verify again against terminal list.
close_terminal() {
  local h="$1" out
  [ -n "$h" ] || return 0
  out="$(oc terminal close --terminal "$h" --json 2>/dev/null)"
  # tab_not_found means a sibling pane already took the whole tab down.
  printf '%s' "$out" | grep -q '"ptyKilled": true' && return 0
  printf '%s' "$out" | grep -q 'tab_not_found' && return 0
  return 1
}

# List every terminal handle in the probe worktree. Cleanup diffs this against
# a snapshot taken before any probe existed, because recorded handles cannot be
# trusted: closing one pane renumbers its siblings in the same tab, and the
# stale handle then reports tab_not_found while the terminal keeps running under
# a new id. That is how a dozen live workers survived a run that reported zero
# leftovers.
# Emits one handle per line on success and prints nothing on failure, so the
# caller MUST distinguish the two by return code. An earlier version swallowed
# every error into an empty result; a single failed `terminal list` then made
# the baseline empty, and cleanup read that as "every terminal here is mine" and
# closed the operator's own sessions. Probing the wrong way round is worse than
# not probing: the fallback has to be "touch nothing".
snapshot_terminals() {
  [ -n "${REPO_PATH:-}" ] || return 1
  local raw
  raw="$(orca terminal list --worktree "path:$REPO_PATH" --json 2>/dev/null)" || return 1
  [ -n "$raw" ] || return 1
  printf '%s' "$raw" | python3 -c "
import json,sys
d=json.load(sys.stdin)
if not d.get('ok'): raise SystemExit(1)
for t in d.get('result',{}).get('terminals',[]):
    h=t.get('handle')
    if h: print(h)
" 2>/dev/null || return 1
}

cleanup() {
  local rc=$? t leaked="" now
  for t in $TERMINALS; do
    close_terminal "$t" || true
  done
  sleep 2

  # Baseline diffing is the only reliable way to find leaks, but it is also the
  # only destructive path in this script. Both snapshots must succeed before it
  # may close anything it did not personally create.
  if [ -z "$BASELINE_OK" ]; then
    printf '[WARN] no usable baseline snapshot; skipping leak sweep entirely\n' >&2
    printf '[WARN] check by hand: orca terminal list --worktree "path:%s" --json\n' "$REPO_PATH" >&2
    FAILED=1
  elif ! now="$(snapshot_terminals)"; then
    printf '[WARN] could not list terminals during cleanup; skipping leak sweep\n' >&2
    FAILED=1
  else
    # Anything in the worktree that was not there when this run started is ours,
    # whatever its handle or title now says.
    for t in $now; do
      printf '%s\n' "$BASELINE_TERMINALS" | grep -qx "$t" && continue
      close_terminal "$t" || true
    done
    sleep 2
    if now="$(snapshot_terminals)"; then
      for t in $now; do
        printf '%s\n' "$BASELINE_TERMINALS" | grep -qx "$t" && continue
        leaked="$leaked $t"
      done
    else
      printf '[WARN] could not verify the leak sweep\n' >&2
      FAILED=1
    fi
  fi
  if [ -n "$leaked" ]; then
    printf '[WARN] terminals still alive after cleanup:%s\n' "$leaked" >&2
    printf '[WARN] close them by hand: orca terminal close --terminal <handle> --json\n' >&2
    FAILED=1
  fi
  for t in $TASK_IDS; do
    oc orchestration task-update --id "$t" --status failed --json >/dev/null 2>&1 || true
  done
  if [ -n "$WORKTREE_ID" ] && [ "$KEEP" -eq 0 ]; then
    oc worktree rm --worktree "$WORKTREE_ID" --force --json >/dev/null 2>&1 || true
  fi
  rm -f /tmp/orca-smoke-ask.json
  # Run binding is per-terminal and sticky. Running this from a Lead terminal
  # that was coordinating real work would otherwise silently steal its Run.
  if [ -n "$PRIOR_RUN" ]; then
    oc orchestration run-use --id "$PRIOR_RUN" --json >/dev/null 2>&1 || true
    printf '[INFO] restored the prior Run binding: %s\n' "$PRIOR_RUN"
  fi
  # Leaked resources are a failure of this harness even when every assertion
  # passed, so the exit status has to say so.
  if [ "$FAILED" -ne 0 ] && [ "$rc" -eq 0 ]; then
    printf '[FAIL] cleanup could not prove the environment was left clean\n' >&2
    exit 1
  fi
}
trap cleanup EXIT

# These set a global instead of printing. Callers used to write
# X="$(new_task ...)", and a command substitution is a subshell, so the
# TASK_IDS/TERMINALS accumulation was discarded every time. The cleanup loop
# that settles tasks was dead code, and every run left a fresh batch of
# dispatched tasks on the reused Run. Third variant of the same subshell bug.
NEW_ID=""
new_task() {
  NEW_ID="$(oc orchestration task-create --task-title "$1" --spec "$2" --json | jget result.task.id)"
  if [ -z "$NEW_ID" ]; then
    fail "task-create returned no id for '$1'"
    return 1
  fi
  TASK_IDS="$TASK_IDS $NEW_ID"
}

new_shell() {
  NEW_ID="$(oc terminal create --worktree "path:$REPO_PATH" --title "$1" --command 'sh' --json | jget result.terminal.handle)"
  if [ -z "$NEW_ID" ]; then
    fail "terminal create returned no handle for '$1'"
    return 1
  fi
  TERMINALS="$TERMINALS $NEW_ID"
}

task_status() {
  oc orchestration task-list --brief --json 2>/dev/null | python3 -c "
import json,sys
for t in json.load(sys.stdin).get('result',{}).get('tasks',[]):
    if t.get('id')==sys.argv[1]: print(t.get('status')); break
" "$1"
}

dispatch_status() {
  oc orchestration dispatch-show --task "$1" --json 2>/dev/null | jget result.dispatch.status
}

# Send a command into a shell terminal and give Orca a moment to apply it.
shell_run() {
  oc terminal send --terminal "$1" --text "$2" --enter --json >/dev/null 2>&1 || true
  sleep "${3:-6}"
}

drain_inbox() {
  local d
  for _ in 1 2 3 4 5 6 7 8; do
    d="$(oc orchestration check --json 2>/dev/null | jget result.deliveryId)"
    [ -n "$d" ] || return 0
    oc orchestration check --ack "$d" --json >/dev/null 2>&1 || return 0
  done
}

# ------------------------------------------------------------- preflight
printf '# Orca orchestration smoke test\n'

command -v orca >/dev/null 2>&1 || { printf '[SKIP] orca is not installed\n'; exit 0; }
if [ "$(oc status --json 2>/dev/null | jget ok)" != "True" ]; then
  printf '[SKIP] Orca runtime is not reachable; start Orca and rerun\n'
  exit 0
fi

# The probe base must be a worktree Orca manages. Falling back to the Skill
# directory looks harmless and is not: terminals still get created, but every
# dispatch against them fails on a worktree mismatch, which reads as "dispatch
# is broken" rather than "that path is not Orca's".
# Computed unconditionally: the close-mode block below needs these candidates
# even when `worktree current` succeeds and the fallback never runs.
LIVE_WORKTREES="$(oc terminal list --json 2>/dev/null | python3 -c "
import json,sys
seen=[]
for t in json.load(sys.stdin).get('result',{}).get('terminals',[]):
    p=t.get('worktreePath')
    if p and not t.get('orphaned') and p not in seen:
        seen.append(p)
print(chr(10).join(seen))
")"

REPO_PATH="$(oc worktree current --json 2>/dev/null | jget result.worktree.path)"
if [ -z "$REPO_PATH" ]; then
  # The base has to clear two bars, and worktree_list[0] clears neither on
  # purpose. It must be open in the Orca UI, because a worktree without a window
  # still accepts `terminal create` and still reports surface=visible while the
  # terminal never gets a tab -- `close --tab` then answers tab_not_found and
  # close-mode reads as a runtime regression. It must also be a main worktree,
  # because the cross-worktree block creates a probe worktree from it, and that
  # fails from inside a worktree. On 2026-08-04 worktree_list[0] was neither:
  # two false FAILs, and after fixing only the first bar, two silently skipped
  # assertions instead. An existing non-orphaned terminal is the only evidence
  # the CLI offers that the UI adopts tabs there.
  REPO_PATH="$(oc worktree list --json 2>/dev/null | LIVE="$LIVE_WORKTREES" python3 -c "
import json,os,sys
live=[l for l in os.environ.get('LIVE','').split(chr(10)) if l]
ws=json.load(sys.stdin).get('result',{}).get('worktrees',[])
main=[w['path'] for w in ws if w.get('path') in live and w.get('isMainWorktree')]
open_=[w['path'] for w in ws if w.get('path') in live]
print((main or open_ or [''])[0])
")"
fi
if [ -z "$REPO_PATH" ]; then
  REPO_PATH="$(oc worktree list --json 2>/dev/null | python3 -c "
import json,sys
ws=json.load(sys.stdin).get('result',{}).get('worktrees',[])
print(ws[0].get('path','') if ws else '')")"
fi
[ -n "$REPO_PATH" ] || { printf '[SKIP] no Orca-managed worktree to probe against\n'; exit 0; }
info "probe worktree base: $REPO_PATH"
if BASELINE_TERMINALS="$(snapshot_terminals)"; then
  BASELINE_OK=1
  info "baseline: $(printf '%s\n' "$BASELINE_TERMINALS" | grep -c . || true) terminal(s) already in that worktree"
else
  printf '[SKIP] cannot snapshot terminals in %s; refusing to run without a baseline\n' "$REPO_PATH" >&2
  exit 2
fi

# --------------------------------------------------- 1. retired commands
# These must still resolve. A Lead copying an old recipe needs a refusal that
# names the migration, not a not-found that reads like a broken install.
for cmd in run run-stop coordinator-start coordinator-stop; do
  code="$(oc orchestration "$cmd" --json 2>&1 | jget error.code)"
  [ "$code" = "orchestration_migration_required" ] \
    && pass "orchestration $cmd is inert as documented" \
    || fail "orchestration $cmd returned '$code', handbook says orchestration_migration_required"
done

# --------------------------------------------------------- 2. Run binding
PRIOR_RUN="$(oc orchestration run-current --json 2>/dev/null | jget result.run.id)"
[ -n "$PRIOR_RUN" ] && info "prior Run binding: $PRIOR_RUN (will be restored)"

EXISTING_RUN="$(oc orchestration run-list --json 2>/dev/null | python3 -c "
import json,sys
for r in json.load(sys.stdin).get('result',{}).get('runs',[]):
    if 'smoke' in (r.get('objective') or '').lower() and not r.get('legacy'):
        print(r.get('id')); break
")"

if [ -n "$EXISTING_RUN" ]; then
  RUN_ID="$EXISTING_RUN"
  RU_OUT="$(oc orchestration run-use --id "$RUN_ID" --json)"
  [ "$(printf '%s' "$RU_OUT" | jget ok)" = "True" ] \
    && pass "run-use reclaims an existing Run without --takeover-legacy ($RUN_ID)" \
    || fail "run-use could not reclaim smoke Run $RUN_ID: $(printf '%s' "$RU_OUT" | jget error.code)"
else
  RUN_ID="$(oc orchestration run-create --objective "$SMOKE_OBJECTIVE" --json 2>/dev/null | jget result.run.id)"
  [ -n "$RUN_ID" ] \
    && pass "run-create created the reusable smoke Run ($RUN_ID)" \
    || { fail "run-create produced no Run; everything below depends on it"; exit 1; }
fi

[ "$(oc orchestration run-current --json 2>/dev/null | jget result.run.id)" = "$RUN_ID" ] \
  && pass "run-current reports the bound Run" \
  || fail "run-current disagrees with the Run this harness just bound"

drain_inbox

# --------------------------- 3. worker_done settles, and only from the assignee
# Driven entirely through shell terminals: no model decides whether this passes.
new_task 'smoke-done' 'deterministic lifecycle probe'
DONE_TASK="$NEW_ID"
new_shell 'smoke-owner'
OWNER_SH="$NEW_ID"
new_shell 'smoke-other'
OTHER_SH="$NEW_ID"
sleep 3

DONE_DISPATCH="$(oc orchestration dispatch --task "$DONE_TASK" --to "$OWNER_SH" --json 2>/dev/null | jget result.dispatch.id)"
[ -n "$DONE_DISPATCH" ] \
  && pass "dispatch tracks a plain shell without --inject" \
  || { fail "dispatch could not track a shell terminal"; exit 1; }

shell_run "$OTHER_SH" "orca orchestration send --type worker_done --subject forged --body forged --task-id $DONE_TASK --dispatch-id $DONE_DISPATCH --outcome succeeded --json"
[ "$(task_status "$DONE_TASK")" = "dispatched" ] \
  && pass "worker_done from a non-assignee terminal does not settle the task" \
  || fail "a foreign terminal settled the task; authority is not assignee-bound after all"

shell_run "$OWNER_SH" "orca orchestration send --type worker_done --subject done --body ok --task-id $DONE_TASK --dispatch-id $DONE_DISPATCH --outcome succeeded --json"
[ "$(task_status "$DONE_TASK")" = "completed" ] \
  && pass "worker_done from the assignee settles the task automatically" \
  || fail "task is '$(task_status "$DONE_TASK")' after an assignee worker_done, expected completed"

[ "$(dispatch_status "$DONE_TASK")" = "completed" ] \
  && pass "the dispatch settles alongside the task" \
  || fail "dispatch is '$(dispatch_status "$DONE_TASK")' after worker_done"

drain_inbox

# --------------------------------------------------- 4. reopening a task
# The handbook's rework rule rests on two facts. The first is asserted here:
# task-update reopens the Task but does not touch the Dispatch, which is why
# reopening alone never re-runs anything.
#
# The second fact -- that --retry-of refuses a *completed* dispatch -- cannot be
# asserted deterministically. It needs a completed dispatch on a recognised
# agent, and a dispatch only reaches completed when its assignee sends
# worker_done; an agent assignee means asking a model to run a command, which is
# exactly the non-determinism this harness exists to avoid. It was verified by
# hand on 2026-08-03 (task_not_startable, both before and after reopening the
# Task) and is recorded in the handbook as a manual finding. Section 8 asserts
# the reachable half: --retry-of is accepted on a failed dispatch.
oc orchestration task-update --id "$DONE_TASK" --status ready --json >/dev/null 2>&1
sleep 1
[ "$(task_status "$DONE_TASK")" = "ready" ] \
  && pass "task-update reopens a completed task" \
  || fail "task is '$(task_status "$DONE_TASK")' after task-update --status ready"
[ "$(dispatch_status "$DONE_TASK")" = "completed" ] \
  && pass "reopening the task leaves the settled dispatch untouched" \
  || fail "dispatch changed to '$(dispatch_status "$DONE_TASK")' when only the task was reopened"
oc orchestration task-update --id "$DONE_TASK" --status completed --json >/dev/null 2>&1

# ------------------------------------------ 5. escalation settles nothing
new_task 'smoke-escalation' 'deterministic escalation probe'
ESC_TASK="$NEW_ID"
new_shell 'smoke-esc'
ESC_SH="$NEW_ID"
sleep 3
ESC_DISPATCH="$(oc orchestration dispatch --task "$ESC_TASK" --to "$ESC_SH" --json 2>/dev/null | jget result.dispatch.id)"
shell_run "$ESC_SH" "orca orchestration send --type escalation --subject 'ctx handoff' --body probe --task-id $ESC_TASK --dispatch-id $ESC_DISPATCH --json"

[ "$(dispatch_status "$ESC_TASK")" = "dispatched" ] \
  && pass "escalation leaves the dispatch unsettled, so the Lead must close it out" \
  || fail "dispatch is '$(dispatch_status "$ESC_TASK")' after escalation, expected dispatched"

drain_inbox

# ------------------------------------------------- 6. ask/reply round trip
# The handbook tells the Lead it must answer questions or the worker blocks.
new_task 'smoke-ask' 'deterministic ask probe'
ASK_TASK="$NEW_ID"
new_shell 'smoke-ask'
ASK_SH="$NEW_ID"
sleep 3
ASK_DISPATCH="$(oc orchestration dispatch --task "$ASK_TASK" --to "$ASK_SH" --json 2>/dev/null | jget result.dispatch.id)"
REPLY_TOKEN="GO-7788"
# `ask` blocks, so fire it and move on rather than waiting for the shell.
oc terminal send --terminal "$ASK_SH" \
  --text "orca orchestration ask --question 'proceed?' --timeout-ms 180000 --json > /tmp/orca-smoke-ask.json 2>&1" \
  --enter --json >/dev/null 2>&1
sleep 10

QUESTION_ID="$(oc orchestration check --peek --json 2>/dev/null | python3 -c "
import json,sys
msgs=json.load(sys.stdin).get('result',{}).get('messages',[])
for m in reversed(msgs):
    if m.get('type')=='question': print(m.get('id')); break
")"
if [ -n "$QUESTION_ID" ]; then
  pass "a worker ask surfaces to the Lead as a question message"
  oc orchestration reply --id "$QUESTION_ID" --body "$REPLY_TOKEN" --json >/dev/null 2>&1
  sleep 10
  if grep -q "$REPLY_TOKEN" /tmp/orca-smoke-ask.json 2>/dev/null; then
    pass "reply unblocks the waiting ask and delivers the answer"
  else
    fail "the blocked ask never received the reply body"
  fi
else
  fail "no question message arrived; the ask/reply path is broken"
fi
drain_inbox

# ------------------------- 7. supervised-only commands refuse everything else
BARE_OUT="$(oc orchestration worker-start --task "$ESC_TASK" --worktree "path:$REPO_PATH" \
  --terminal "$ESC_SH" --timeout-ms 60000 --json 2>&1)"
expect_error "worker-start on a bare shell" "agent_unconfigured" "$BARE_OUT"

AB_OUT="$(oc orchestration worker-abandon --dispatch "$ESC_DISPATCH" --json 2>/dev/null)"
if printf '%s' "$AB_OUT" | grep -q '"ok": true'; then
  fail "worker-abandon accepted a dispatch that has no supervised worker"
else
  pass "worker-abandon refuses a non-supervised dispatch ($(printf '%s' "$AB_OUT" | jget error.code))"
fi

# ------------------- 8. real agent: attach and stop, without it deciding anything
AGENT_TERM="$(oc terminal create --worktree "path:$REPO_PATH" --title 'smoke-agent' --command "$PROBE_AGENT" --json 2>/dev/null | jget result.terminal.handle)"
TERMINALS="$TERMINALS $AGENT_TERM"
TW_OUT="$(oc terminal wait --terminal "$AGENT_TERM" --for tui-idle --timeout-ms 120000 --json)"
[ "$(printf '%s' "$TW_OUT" | jget result.wait.satisfied)" = "True" ] \
  && pass "agent TUI reached tui-idle" \
  || fail "agent TUI never became idle (satisfied=$(printf '%s' "$TW_OUT" | jget result.wait.satisfied))"

new_task 'smoke-stop' 'Wait quietly. Do nothing.'
STOP_TASK="$NEW_ID"
WS_OUT="$(oc orchestration worker-start --task "$STOP_TASK" --worktree "path:$REPO_PATH" \
  --terminal "$AGENT_TERM" --timeout-ms 120000 --json 2>/dev/null)"
STOP_DISPATCH="$(printf '%s' "$WS_OUT" | jget result.dispatchId)"

if [ -n "$STOP_DISPATCH" ]; then
  pass "worker-start attached a custom-argv agent terminal"
  printf '%s' "$WS_OUT" | grep -q '"action": "reused"' \
    && pass "worker-start reused the terminal instead of creating one, so custom argv survives" \
    || fail "worker-start did not reuse the terminal; the launch tier would be lost"

  [ "$(oc orchestration dispatch-show --task "$STOP_TASK" --json 2>/dev/null | jget result.dispatch.id)" = "$STOP_DISPATCH" ] \
    && pass "dispatch-show recovers the dispatch id from the task id" \
    || fail "dispatch-show did not return the dispatch worker-start created"

  STOP_OUT="$(oc orchestration worker-stop --dispatch "$STOP_DISPATCH" --json 2>/dev/null)"
  sleep 3
  printf '%s' "$STOP_OUT" | grep -q 'closed_agent_terminal' \
    && pass "worker-stop closes the agent terminal itself" \
    || fail "worker-stop did not report closing the agent terminal"
  [ "$(dispatch_status "$STOP_TASK")" = "failed" ] \
    && pass "worker-stop settles the dispatch as failed" \
    || fail "dispatch is '$(dispatch_status "$STOP_TASK")' after worker-stop, expected failed"
  [ "$(task_status "$STOP_TASK")" = "blocked" ] \
    && pass "worker-stop blocks the task without a manual task-update" \
    || fail "task is '$(task_status "$STOP_TASK")' after worker-stop, expected blocked"
  TERMINALS="$(printf '%s' "$TERMINALS" | sed "s|$AGENT_TERM||")"

  # worker-stop left a failed dispatch, which is precisely what --retry-of is
  # for. Placement is never inherited, so a fresh agent terminal is required.
  RETRY_TERM="$(oc terminal create --worktree "path:$REPO_PATH" --title 'smoke-retry' --command "$PROBE_AGENT" --json 2>/dev/null | jget result.terminal.handle)"
  TERMINALS="$TERMINALS $RETRY_TERM"
  oc terminal wait --terminal "$RETRY_TERM" --for tui-idle --timeout-ms 120000 --json >/dev/null 2>&1 || true
  RETRY_OUT="$(oc orchestration worker-start --task "$STOP_TASK" --worktree "path:$REPO_PATH" \
    --terminal "$RETRY_TERM" --retry-of "$STOP_DISPATCH" --timeout-ms 60000 --json 2>/dev/null)"
  if printf '%s' "$RETRY_OUT" | grep -q '"ok": true'; then
    pass "--retry-of is accepted on a failed dispatch, with explicit placement"
  else
    fail "--retry-of was refused on a failed dispatch: $(printf '%s' "$RETRY_OUT" | jget error.code)"
  fi
else
  fail "worker-start could not attach the agent terminal"
fi
drain_inbox

# ------------------------------ 9. cross-worktree dispatch needs --worktree
WT_OUT="$(oc worktree create --repo "path:$REPO_PATH" --name 'smoke-cross-worktree' --no-parent --setup run --json 2>/dev/null || true)"
WORKTREE_ID="$(printf '%s' "$WT_OUT" | jget result.worktree.id)"
if [ -n "$WORKTREE_ID" ]; then
  SHELL_COUNT="$(oc terminal list --worktree "id:$WORKTREE_ID" --json 2>/dev/null | python3 -c "
import json,sys; print(len(json.load(sys.stdin).get('result',{}).get('terminals',[])))")"
  info "a worktree created without --agent carries $SHELL_COUNT terminal(s) here; the handbook says not to assume a number"

  FAR_TERM="$(oc terminal create --worktree "id:$WORKTREE_ID" --title 'smoke-far' --command "$PROBE_AGENT" --json 2>/dev/null | jget result.terminal.handle)"
  TERMINALS="$TERMINALS $FAR_TERM"
  new_task 'smoke-far' 'Protocol probe only. Do nothing at all and do not report anything.'
  FAR_TASK="$NEW_ID"
  oc terminal wait --terminal "$FAR_TERM" --for tui-idle --timeout-ms 120000 --json >/dev/null 2>&1 || true

  FAR_OUT="$(oc orchestration worker-start --task "$FAR_TASK" --terminal "$FAR_TERM" --timeout-ms 60000 --json 2>&1)"
  expect_error "worker-start on a terminal outside the Lead's worktree" "terminal_worktree_mismatch" "$FAR_OUT"

  oc orchestration worker-start --task "$FAR_TASK" --worktree "id:$WORKTREE_ID" \
    --terminal "$FAR_TERM" --timeout-ms 60000 --json 2>/dev/null | grep -q '"ok": true' \
    && pass "the same call succeeds once --worktree is supplied" \
    || fail "worker-start still failed with an explicit --worktree"
  # That call really did dispatch, so settle it rather than leaving an agent
  # sitting on an open assignment.
  FAR_DISPATCH="$(oc orchestration dispatch-show --task "$FAR_TASK" --json 2>/dev/null | jget result.dispatch.id)"
  [ -n "$FAR_DISPATCH" ] && oc orchestration worker-stop --dispatch "$FAR_DISPATCH" --json >/dev/null 2>&1 || true
else
  info "could not create a probe worktree; skipped the cross-worktree assertions"
fi
drain_inbox

# -------------------------------- 9b. closing a terminal must kill the pty
# This is the assertion that cost the most: the harness used `close --tab`
# everywhere, believed its own "zero leftovers" report, and left a dozen live
# workers running for the better part of an hour.
# These three need a worktree the Orca UI has open, which is not the same
# property the rest of the run needs -- the cross-worktree block above needs a
# *main* worktree, and on this machine no worktree is both. Worse, the CLI
# cannot tell them apart: a worktree with no window still returns
# surface=visible on create. Creating a terminal and closing it is the only
# probe that answers the question, so try each candidate until one has a tab.
# A unique sleep duration doubles as a process marker, so pgrep can answer the
# only question that matters here -- is it actually still running. `ptyKilled`
# cannot: on 2026-08-04 it reported false while killing a plain sleep, a sleep
# that ignores SIGHUP, and a real Opus 5 Worker. Anchored with $ so that marker
# 9123 is not matched by an unrelated `sleep 91234`.
PROBE_SEQ=0
start_marked_probe() {
  PROBE_SEQ=$((PROBE_SEQ + 1))
  PROBE_SECS=$(( 9000 + ($$ % 400) * 2 + PROBE_SEQ ))
  PROBE_HANDLE="$(oc terminal create --worktree "path:$1" --title 'smoke-close' --command "sleep $PROBE_SECS" --json | jget result.terminal.handle)"
  [ -n "$PROBE_HANDLE" ] || return 1
  TERMINALS="$TERMINALS $PROBE_HANDLE"
  sleep 3
  # If it never started there is no way to tell a killed process from one that
  # was never alive, so the assertions must not run against this base.
  pgrep -f "sleep $PROBE_SECS\$" >/dev/null 2>&1
}
drop_probe() {
  oc terminal close --terminal "$1" --json >/dev/null 2>&1
  TERMINALS="$(printf '%s' "$TERMINALS" | sed "s|$1||")"
}

# Deliberately does NOT exercise `close --tab`. That flag tears down the whole
# tab container: it takes every other pane in the tab with it -- on 2026-08-04 it
# silently killed a user's unrelated shell -- and afterwards this worktree cannot
# hand out a tab at all, so even a plain close answers tab_not_found until the UI
# opens a new one. A smoke test must not leave that behind. The handbook's ban on
# --tab is held by the BANNED phrase list and a fixture injection instead.
close_mode_checks() {
  local base="$1" handle secs out
  start_marked_probe "$base" || { [ -n "${PROBE_HANDLE:-}" ] && drop_probe "$PROBE_HANDLE"; return 1; }
  handle="$PROBE_HANDLE"; secs="$PROBE_SECS"

  out="$(oc terminal close --terminal "$handle" --json 2>/dev/null)"
  if printf '%s' "$out" | grep -q 'tab_not_found'; then
    # This worktree has no tab to give -- either never opened in the UI, or a
    # previous --tab tore its container down. Try another base.
    drop_probe "$handle"
    return 1
  fi
  sleep 4

  # Asserted against the OS, not against what Orca reported. ptyKilled was
  # observed reporting false while killing a plain sleep, a sleep that ignores
  # SIGHUP, and a real Opus 5 Worker, so it cannot carry this assertion.
  pgrep -f "sleep $secs\$" >/dev/null 2>&1 \
    && fail "close left the process running; a Worker reclaimed this way would keep going unseen" \
    || pass "close without --tab actually kills the pty process"

  printf '%s' "$out" | grep -q '"ptyKilled": true' \
    && pass "close without --tab reports ptyKilled:true, matching what it did" \
    || fail "close without --tab did not report ptyKilled:true"

  # The completion criteria tell the Lead to verify with show rather than with
  # list alone, so show has to actually answer.
  oc terminal show --terminal "$handle" --json 2>/dev/null | grep -q '"connected": false' \
    && pass "terminal show reports connected:false once the terminal is closed" \
    || fail "terminal show does not report connected:false; the completion criteria depend on it"

  sleep 2
  oc terminal list --json 2>/dev/null | grep -q "$handle" \
    && fail "the terminal is still listed after the close" \
    || pass "the terminal is gone from terminal list after the plain close"
  TERMINALS="$(printf '%s' "$TERMINALS" | sed "s|$handle||")"
  return 0
}

CLOSE_MODE_BASE=""
while IFS= read -r cand; do
  [ -n "$cand" ] || continue
  if close_mode_checks "$cand"; then CLOSE_MODE_BASE="$cand"; break; fi
done <<EOF
$REPO_PATH
$LIVE_WORKTREES
EOF
if [ -n "$CLOSE_MODE_BASE" ]; then
  [ "$CLOSE_MODE_BASE" = "$REPO_PATH" ] || info "close-mode ran against $CLOSE_MODE_BASE; the probe base has no open tab"
else
  # Said out loud on purpose. This is the assertion that cost the most -- the
  # harness once used `close --tab` everywhere, believed its own zero-leftovers
  # report, and left a dozen live workers running for the better part of an
  # hour. A silent skip would read as "still covered".
  info "no candidate worktree has an open tab in the Orca UI, so close-mode was NOT exercised this run; open one of them in Orca and rerun"
fi

# ---------------------------------------- 10. routing tiers actually apply
# The reason this Skill exists is to put model, effort and context onto the
# worker. Starting a session and reading its own banner costs almost nothing
# because no task is dispatched and the session closes immediately.
# Searches only the output that comes AFTER the echoed launch command. The
# model name is in that command because we put it there, so a plain grep over
# the whole buffer proves the string was requested, not that the session is
# running on it -- and this Skill exists precisely because `claude-opus-5` and
# `claude-opus-5[1m]` request identically and run differently. An error banner
# like "unknown model: X" would also have matched.
session_reports() {
  local out="$1" needle="$2"
  printf '%s' "$out" | python3 -c "
import json,sys
needle=sys.argv[1]
try:
    d=json.load(sys.stdin)
except Exception:
    print('no'); raise SystemExit(0)
tail=d.get('result',{}).get('terminal',{}).get('tail',[])
start=0
for i,l in enumerate(tail):
    if 'dangerously' in l:
        start=i+1
print('yes' if any(needle in l for l in tail[start:]) else 'no')
" "$needle"
}

assert_tier() {
  local label="$1" title="$2" command="$3" model="$4" effort="$5" handle out want_ctx ctx
  if [ -z "$model" ] || [ -z "$effort" ]; then
    fail "$label: empty expectation, refusing to assert"
    return
  fi
  handle="$(oc terminal create --worktree "path:$REPO_PATH" --title "$title" --command "$command" --json | jget result.terminal.handle)"
  [ -n "$handle" ] || { fail "$label: could not start a session"; return; }
  TERMINALS="$TERMINALS $handle"
  oc terminal wait --terminal "$handle" --for tui-idle --timeout-ms 120000 --json >/dev/null 2>&1 || true
  sleep 8
  out="$(oc terminal read --terminal "$handle" --cursor 0 --limit 400 --json)"
  # A CLI that rejects the tier echoes the model name back inside its error, so
  # "the name appears after the launch line" is necessary but not sufficient.
  # Reject the session outright if it is complaining about the model.
  if [ "$(session_reports "$out" "unknown model")" = "yes" ] \
     || [ "$(session_reports "$out" "Invalid model")" = "yes" ] \
     || [ "$(session_reports "$out" "invalid model")" = "yes" ]; then
    fail "$label: the CLI rejected the model; the tier is not usable"
    close_terminal "$handle" || true
    TERMINALS="$(printf '%s' "$TERMINALS" | sed "s|$handle||")"
    return
  fi
  case "$command" in
    claude\ *)
      # Claude Code 2.1.226 stopped printing the raw model id in the TUI, so
      # grepping the banner for it proves nothing either way. modelUsage from a
      # headless probe is the authoritative signal and stronger than the banner
      # ever was: the requested string must come back as the usage key, and a
      # [1m] model must actually widen the window -- which is the difference
      # this Skill exists to deliver. claude-opus-5 and claude-opus-5[1m]
      # request identically; only the window tells them apart.
      want_ctx=200000
      case "$model" in *"[1m]"*) want_ctx=1000000 ;; esac
      ctx="$(claude --model "$model" -p 'reply with exactly: ok' --output-format json 2>/dev/null | python3 -c "
import json,sys
try:
    mu=json.load(sys.stdin).get('modelUsage',{})
except Exception:
    mu={}
print(mu.get(sys.argv[1],{}).get('contextWindow',''))
" "$model")"
      [ "$ctx" = "$want_ctx" ] \
        && pass "$label runs on '$model' with contextWindow $want_ctx by headless probe" \
        || fail "$label probe reported contextWindow '$ctx' for '$model', expected $want_ctx; the tier is not being delivered"
      ;;
    *)
      [ "$(session_reports "$out" "$model")" = "yes" ] \
        && pass "$label runs on '$model' by its own report, not by command echo" \
        || fail "$label never reported '$model' after the launch line; the tier is not being delivered"
      ;;
  esac
  [ "$(session_reports "$out" "$effort")" = "yes" ] \
    && pass "$label reports effort '$effort'" \
    || fail "$label never reported effort '$effort'"
  close_terminal "$handle" || true
  TERMINALS="$(printf '%s' "$TERMINALS" | sed "s|$handle||")"
}

assert_tier "the Opus tier" "smoke-tier-opus" \
  "claude --model '$(fact LEAD_MODEL)' --effort $(fact LEAD_EFFORT) --dangerously-skip-permissions" \
  "$(fact LEAD_MODEL)" "$(fact LEAD_EFFORT)"

# No service_tier here, and that absence is the assertion. The handbook bans Fast
# in all three Codex tiers -- it is a 1.5x-speed-for-increased-usage multiplier
# applied to every token -- and check-consistency.sh fails any documented command
# that carries it. This probe kept it anyway, left over from before the ban, and
# nothing objected because the consistency checker only scans the docs. A probe
# that launches a tier the Lead is forbidden to dispatch proves nothing about the
# tier the Lead actually gets.
assert_tier "the Codex tier" "smoke-tier-codex" \
  "codex --model $(fact CODEX_MODEL) -c model_context_window=$(fact CODEX_CONTEXT_WINDOW) -c model_reasoning_effort=\"$(fact CODEX_EFFORT)\" --dangerously-bypass-approvals-and-sandbox" \
  "$(fact CODEX_MODEL)" "$(fact CODEX_EFFORT)"

printf '\n'
if [ "$FAILED" -eq 0 ]; then
  printf '[DONE] every documented orchestration behaviour held\n'
  exit 0
fi
printf '[FAIL] the handbook disagrees with the runtime above\n' >&2
exit 1
