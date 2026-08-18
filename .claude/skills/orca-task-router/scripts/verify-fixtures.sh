#!/usr/bin/env bash
# Input: none.
# Output: fixture verification for dry-run, apply, idempotency, and verify.
# Exit code: 0 when all checks pass; non-zero on failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
INSTALLER="$SCRIPT_DIR/install.sh"
FIXTURE_HOME="$(mktemp -d)"
trap 'rm -rf "$FIXTURE_HOME"' EXIT

HOME="$FIXTURE_HOME" "$INSTALLER" --dry-run >/dev/null

if [ -e "$FIXTURE_HOME/.claude/skills/orca-task-router" ]; then
  printf 'dry-run changed the fixture home\n' >&2
  exit 1
fi

HOME="$FIXTURE_HOME" "$INSTALLER" --apply >/dev/null
HOME="$FIXTURE_HOME" "$INSTALLER" --verify >/dev/null
HOME="$FIXTURE_HOME" "$INSTALLER" --apply >/dev/null

test -L "$FIXTURE_HOME/.claude/skills/orca-task-router"
test -L "$FIXTURE_HOME/.Codex/skills/orca-task-router"
cmp -s \
  "$SCRIPT_DIR/../assets/claude/rules/orca-task-routing.md" \
  "$FIXTURE_HOME/.claude/rules/orca-task-routing.md"
cmp -s \
  "$SCRIPT_DIR/../assets/claude/orca-lead-system-prompt.txt" \
  "$FIXTURE_HOME/.config/claude-cc/orca-lead-system-prompt.txt"

CONSISTENCY="$SCRIPT_DIR/check-consistency.sh"
"$CONSISTENCY" >/dev/null

# A checker that always passes is worse than no checker. Inject each drift this
# Skill has actually suffered and require a non-zero exit. Injection goes
# through python because the launch commands contain nested shell quoting that
# sed mangles silently, which once made a working check look like a miss.
DRIFT_ROOT="$(mktemp -d)"
trap 'rm -rf "$FIXTURE_HOME" "$DRIFT_ROOT"' EXIT

expect_drift_caught() {
  local label="$1" mutation="$2"
  rm -rf "$DRIFT_ROOT/skill"
  cp -R "$SCRIPT_DIR/.." "$DRIFT_ROOT/skill"
  ( cd "$DRIFT_ROOT/skill" && python3 -c "$mutation" ) || {
    printf 'drift injection failed to apply: %s\n' "$label" >&2
    exit 1
  }
  if ( cd "$DRIFT_ROOT/skill" && ./scripts/check-consistency.sh >/dev/null 2>&1 ); then
    printf 'consistency checker missed drift: %s\n' "$label" >&2
    exit 1
  fi
}

expect_drift_caught 'lead model loses the [1m] suffix' '
open("assets/claude/orca-lead-model","w").write("claude-opus-5\n")'

expect_drift_caught 'lead effort no longer matches the facts' '
open("assets/claude/orca-lead-effort","w").write("high\n")'

expect_drift_caught 'teammate definition loses the [1m] suffix' '
p="assets/claude/agents/orca-opus-reviewer.md"
s=open(p,encoding="utf-8").read()
n=s.replace("model: claude-opus-5[1m]","model: claude-opus-5")
assert n!=s
open(p,"w",encoding="utf-8").write(n)'

# A teammate that quietly gains write tools breaks the one promise the routing
# rules make about Agent Teams: that changing files requires a Terminal
# Worker. Nothing else here would notice -- the model pin, the install and every
# doc cross-reference stay valid while the agent becomes able to edit the repo.
expect_drift_caught 'read-only teammate gains write tools' '
p="assets/claude/agents/orca-opus-reviewer.md"
s=open(p,encoding="utf-8").read()
n=s.replace("tools: Read, Grep, Glob","tools: Read, Grep, Glob, Write, Edit")
assert n!=s
open(p,"w",encoding="utf-8").write(n)'

# Substring matching would let this one through: NotebookEdit contains "Edit"
# but a naive *Edit* blacklist reads it as already covered, and a naive
# whitelist that greps for known-good names never sees it at all.
expect_drift_caught 'teammate gains NotebookEdit, which hides inside an Edit substring test' '
p="assets/claude/agents/orca-opus-investigator.md"
s=open(p,encoding="utf-8").read()
n=s.replace("tools: Read, Grep, Glob, Bash","tools: Read, Grep, Glob, Bash, NotebookEdit")
assert n!=s
open(p,"w",encoding="utf-8").write(n)'

# The mirror hazard on the read side: BashOutput satisfies a *Bash* substring
# test, so a teammate could pick up a tool nobody vetted while still looking
# like it only has the four allowed ones.
expect_drift_caught 'teammate gains BashOutput, which satisfies a Bash substring test' '
p="assets/claude/agents/orca-opus-reviewer.md"
s=open(p,encoding="utf-8").read()
n=s.replace("tools: Read, Grep, Glob","tools: Read, Grep, Glob, BashOutput")
assert n!=s
open(p,"w",encoding="utf-8").write(n)'

# An agent with no tools field inherits whatever the parent grants, which on
# this Skill means a lead running with bypass flags. Absence has to fail loudly.
expect_drift_caught 'teammate loses its tools field entirely' '
p="assets/claude/agents/orca-opus-investigator.md"
s=open(p,encoding="utf-8").read()
n=s.replace("tools: Read, Grep, Glob, Bash\n","")
assert n!=s
open(p,"w",encoding="utf-8").write(n)'

# Injects the *nested* quoting that every real launch command uses, not the
# flat form. The previous version of this fixture replaced the whole token with
# a flat `--model fable`, which the old extractor could see -- so the fixture
# passed while the check was blind to the only form that appears in practice.
expect_drift_caught 'nested launch command falls back to a bare alias' '
p="references/orca-operations.md"
s=open(p,encoding="utf-8").read()
q=chr(39); bs=chr(92)
wrap=q+bs+q+q
old="--model "+wrap+"claude-fable-5[1m]"+wrap
new="--model "+wrap+"fable"+wrap
assert old in s, "nested form not found; fixture is testing nothing"
n=s.replace(old,new)
assert n!=s
open(p,"w",encoding="utf-8").write(n)'

expect_drift_caught 'a BANNED entry that starts with a dash goes unchecked' '
p="references/routing-policy.md"
s=open(p,encoding="utf-8").read()
open(p,"w",encoding="utf-8").write(s+"\n- 用--max-concurrent 3限制并发。\n")'

expect_drift_caught 'a retired claim reappears' '
open("references/routing-policy.md","a",encoding="utf-8").write("\n- 主Agent固定使用Fable。\n")'

expect_drift_caught 'context guard threshold diverges from the facts' '
p="assets/claude/bin/orca-context-guard.py"
s=open(p,encoding="utf-8").read()
n=s.replace("DEFAULT_THRESHOLD = 300_000","DEFAULT_THRESHOLD = 150_000")
assert n!=s
open(p,"w",encoding="utf-8").write(n)'

expect_drift_caught 'the Lead loses its terminal reclaim grant' '
p="SKILL.md"
s=open(p,encoding="utf-8").read()
n=s.replace("  - Bash(orca terminal close:*)\n","")
assert n!=s
open(p,"w",encoding="utf-8").write(n)'

# The Lead runs this one mid-dispatch, so losing the grant does not fail --
# it stops and waits for a human who is not watching.
expect_drift_caught 'the guard installer loses its execute grant' '
p="SKILL.md"
s=open(p,encoding="utf-8").read()
n="".join(l for l in s.splitlines(keepends=True) if "install-project-guard.sh" not in l)
assert n!=s
open(p,"w",encoding="utf-8").write(n)'

expect_drift_caught 'the Lead loses the grant that tells a stale shell from a live worker' '
p="SKILL.md"
s=open(p,encoding="utf-8").read()
n=s.replace("  - Bash(orca terminal show:*)\n","")
assert n!=s
open(p,"w",encoding="utf-8").write(n)'

expect_drift_caught 'the project guard installer disappears' '
import os
os.remove("scripts/install-project-guard.sh")'

expect_drift_caught 'the handbook stops mentioning the project guard installer' '
p="references/orca-operations.md"
s=open(p,encoding="utf-8").read()
n=s.replace("install-project-guard.sh","some-other-script.sh")
assert n!=s
open(p,"w",encoding="utf-8").write(n)'

expect_drift_caught 'the project guard installer moves off PostToolUse' '
p="scripts/install-project-guard.sh"
s=open(p,encoding="utf-8").read()
n=s.replace("PostToolUse","PreToolUse")
assert n!=s
open(p,"w",encoding="utf-8").write(n)'

expect_drift_caught 'the statusline that feeds the guard disappears' '
import os
os.remove("assets/claude/statusline-command.sh")'

expect_drift_caught 'the statusline stops publishing context facts' '
p="assets/claude/statusline-command.sh"
s=open(p,encoding="utf-8").read()
n=s.replace("claude-ctx-","claude-unused-")
assert n!=s
open(p,"w",encoding="utf-8").write(n)'

expect_drift_caught 'the behavioural smoke test disappears' '
import os
os.remove("scripts/smoke-orchestration.sh")'

expect_drift_caught 'the Skill stops telling maintainers to run the smoke test' '
p="SKILL.md"
s=open(p,encoding="utf-8").read()
n=s.replace("scripts/smoke-orchestration.sh","scripts/some-other-check.sh")
assert n!=s
open(p,"w",encoding="utf-8").write(n)'

expect_drift_caught 'the eval set falls behind the Skill version' '
import json
p="evals/evals.json"
d=json.load(open(p,encoding="utf-8"))
d["version"]="0.0.1"
json.dump(d,open(p,"w",encoding="utf-8"),ensure_ascii=False,indent=2)'

# The eval set is only reusable by skill-creator's runner while it keeps that
# runner's field names. Dropping one silently turns the whole set back into a
# private format that no harness can execute.
expect_drift_caught 'the eval set drifts off the skill-creator schema' '
import json
p="evals/evals.json"
d=json.load(open(p,encoding="utf-8"))
d["evals"][0].pop("expected_output")
json.dump(d,open(p,"w",encoding="utf-8"),ensure_ascii=False,indent=2)'

expect_drift_caught 'the eval set loses its skill_name binding' '
import json
p="evals/evals.json"
d=json.load(open(p,encoding="utf-8"))
d.pop("skill_name",None)
json.dump(d,open(p,"w",encoding="utf-8"),ensure_ascii=False,indent=2)'

# Negative expectations are written as 没有X and exempted from the BANNED-phrase
# scan on purpose. A positive one carrying the same retired claim is real drift,
# so the exemption has to stay narrow enough to still catch this.
expect_drift_caught 'an eval expectation adopts a retired claim' '
import json
p="evals/evals.json"
d=json.load(open(p,encoding="utf-8"))
d["evals"][0]["expectations"].append("主Agent固定使用Fable")
json.dump(d,open(p,"w",encoding="utf-8"),ensure_ascii=False,indent=2)'

expect_drift_caught 'the handbook goes back to recommending close --tab' '
p="references/orca-operations.md"
s=open(p,encoding="utf-8").read()
old="收Worker一律用不带`--tab`的`close`"
assert old in s, "the --tab prohibition moved; this fixture is testing nothing"
n=s.replace(old,"要连tab一起收掉时加`--tab`")
assert n!=s
open(p,"w",encoding="utf-8").write(n)'

expect_drift_caught 'the smoke harness reverts to close --tab in its cleanup' '
p="scripts/smoke-orchestration.sh"
s=open(p,encoding="utf-8").read()
n=s.replace("out=\"$(oc terminal close --terminal \"$h\" --json 2>/dev/null)\"",
            "out=\"$(oc terminal close --terminal \"$h\" --tab --json 2>/dev/null)\"")
assert n!=s
open(p,"w",encoding="utf-8").write(n)'

# ------------------------------------------ smoke harness Fast ban (v0.17.0)
# The harness sits outside RULE_FILES, so the doc-level Fast ban never reached
# it. Its Codex probe carried service_tier="fast" for two versions after the ban
# went in, and the only thing that noticed was a human reading the file.
expect_drift_caught 'the smoke harness turns Fast back on in its Codex probe' '
p="scripts/smoke-orchestration.sh"
s=open(p,encoding="utf-8").read()
d=chr(34)
old="-c model_reasoning_effort=" + chr(92) + d
assert old in s, "codex probe form not found; fixture is testing nothing"
new="-c service_tier=" + chr(92) + d + "fast" + chr(92) + d + " " + old
open(p,"w",encoding="utf-8").write(s.replace(old,new,1))'

expect_drift_caught 'codex drops back to xhigh' '
p="references/orca-operations.md"
s=open(p,encoding="utf-8").read()
n=s.replace(chr(34)+"ultra"+chr(34),chr(34)+"xhigh"+chr(34))
assert n!=s
open(p,"w",encoding="utf-8").write(n)'

# Until v0.13.0 this rule lived only as an implication of the three launch
# recipes, so deleting one sentence of prose removed it with nothing objecting.
expect_drift_caught 'SKILL.md stops stating where a Worker terminal starts' '
p="SKILL.md"
s=open(p,encoding="utf-8").read()
n=s.replace("默认起在Lead","起在合适的")
assert n!=s
open(p,"w",encoding="utf-8").write(n)'

# The quiet half of the worktree contract. A recipe without --worktree still
# runs, still returns a handle, and puts the Worker wherever Orca last called
# active -- the diff then lands in a checkout nobody is watching.
expect_drift_caught 'a launch recipe drops --worktree and floats to the active worktree' '
p="references/orca-operations.md"
s=open(p,encoding="utf-8").read()
q=chr(39); bs=chr(92)
old="--worktree "+q+"path:/absolute/path/to/repo"+q+" "+bs+"\n"
assert old in s, "recipe form not found; fixture is testing nothing"
n=s.replace(old,"",1)
assert n!=s
open(p,"w",encoding="utf-8").write(n)'

# The second-version criterion. It reads as a question about form capability,
# which both forms satisfy, so anything unwilling to rerun from scratch got
# routed to a Terminal. Retired in v0.13.0; design-rationale quotes it inside an
# allow block to explain why, and nowhere else may.
expect_drift_caught 'the retired from-the-breakpoint criterion comes back' '
p="references/routing-policy.md"
s=open(p,encoding="utf-8").read()
open(p,"w",encoding="utf-8").write(s+"\n- 判据是失败之后需不需要从中断点继续。\n")'

# --------------------------------------------- model guard (v0.14.0)
# The guard denies Agent spawns that pin a model. Every link in that chain is
# a separate failure mode, and all of them fail silently: a deleted script
# leaves an else-branch that swallows stdin, a wrong event hands the hook no
# tool_input, a wrong matcher never invokes it at all.

expect_drift_caught 'the model guard script disappears' '
import os
os.remove("assets/claude/bin/orca-model-guard.py")'

expect_drift_caught 'the model guard is registered on the wrong event' '
p="scripts/install.sh"
s=open(p,encoding="utf-8").read()
n=s.replace(chr(34)+"PreToolUse"+chr(34),chr(34)+"PostToolUse"+chr(34))
assert n!=s, "PreToolUse registration not found; fixture is testing nothing"
open(p,"w",encoding="utf-8").write(n)'

expect_drift_caught 'the model guard stops matching the Agent tool' '
p="scripts/install.sh"
s=open(p,encoding="utf-8").read()
q=chr(34)
old=q+"matcher"+q+": "+q+"Agent"+q
assert old in s, "matcher form not found; fixture is testing nothing"
open(p,"w",encoding="utf-8").write(s.replace(old,q+"matcher"+q+": "+q+"*"+q))'

# Indented on purpose: the first version of this assertion used \s, which BSD
# grep does not support in an ERE, so an allow-list one tab in sailed through.
expect_drift_caught 'the model guard grows an indented allow-list' '
p="assets/claude/bin/orca-model-guard.py"
open(p,"a",encoding="utf-8").write("\n    ALLOWED = [" + chr(34) + "opus" + chr(34) + "]\n")'

expect_drift_caught 'the model guard stops checking tool_name' '
p="assets/claude/bin/orca-model-guard.py"
s=open(p,encoding="utf-8").read()
assert "tool_name" in s, "tool_name check not found; fixture is testing nothing"
open(p,"w",encoding="utf-8").write(s.replace("tool_name","tool_kind"))'

expect_drift_caught 'the guard sends denied callers at a model the facts dropped' '
p="assets/claude/bin/orca-model-guard.py"
s=open(p,encoding="utf-8").read()
assert "claude-opus-5[1m]" in s, "lead model not named; fixture is testing nothing"
open(p,"w",encoding="utf-8").write(s.replace("claude-opus-5[1m]","claude-opus-4-8[1m]"))'

expect_drift_caught 'a superseded version literal appears outside an allow block' '
p="references/routing-policy.md"
s=open(p,encoding="utf-8").read()
open(p,"w",encoding="utf-8").write(s+"\n对照Claude Code 2.1.217的行为。\n")'

# ------------------------------------------- Codex tiers (v0.15.0)
# Downshifting switches model and keeps effort pinned. Each of these mutations
# is a way that intent quietly inverts: Fast creeping back multiplies the whole
# bill, an effort that the model does not support fails at launch, and the 1M
# context window is a claim that was false for months without anyone noticing.

expect_drift_caught 'a codex command turns Fast back on' '
p="references/session-launch-guide.md"
s=open(p,encoding="utf-8").read()
q=chr(39); d=chr(34)
old="-c "+q+"model_reasoning_effort="+d+"ultra"+d+q+" --dangerously-bypass"
assert old in s, "sol command form not found; fixture is testing nothing"
new="-c "+q+"model_reasoning_effort="+d+"ultra"+d+q+" -c "+q+"service_tier="+d+"fast"+d+q+" --dangerously-bypass"
open(p,"w",encoding="utf-8").write(s.replace(old,new,1))'

# Plain string surgery, not re.sub: a backreference here needs backslashes that
# survive shell single quotes AND python string parsing, and getting that wrong
# silently replaced the model name itself, which took the line out of the
# checker scope and made a real miss look like a checker bug.
expect_drift_caught 'the thrift tier is given an effort its model does not support' '
p="references/session-launch-guide.md"
s=open(p,encoding="utf-8").read()
q=chr(39); d=chr(34)
old="gpt-5.6-luna -c model_context_window=272000 -c "+q+"model_reasoning_effort="+d+"max"+d
assert old in s, "luna command form not found; fixture is testing nothing"
new=old.replace(d+"max"+d, d+"ultra"+d)
open(p,"w",encoding="utf-8").write(s.replace(old,new,1))'

expect_drift_caught 'a codex command claims the 1M context window again' '
p="references/session-launch-guide.md"
s=open(p,encoding="utf-8").read()
n=s.replace("model_context_window=272000","model_context_window=1000000",1)
assert n!=s, "context window form not found; fixture is testing nothing"
open(p,"w",encoding="utf-8").write(n)'

expect_drift_caught 'the codex quota probe disappears' '
import os
os.remove("assets/claude/bin/orca-codex-usage.py")'

expect_drift_caught 'the probe thresholds drift away from the facts' '
p="assets/claude/bin/orca-codex-usage.py"
s=open(p,encoding="utf-8").read()
q=chr(34)
n=s.replace(q+"60"+q,q+"50"+q,1)
assert n!=s, "terra threshold not found; fixture is testing nothing"
open(p,"w",encoding="utf-8").write(n)'

# ---------------------------------------- closure routing (v0.16.0)
# Implementation is routed by spec closure, Claude workers run high, and the
# 4.8 tiers are implementation xhigh plus disaster-standby max. Each of these
# mutations is a way the old world quietly returns: a worker copying the
# lead's max still launches fine, an effort slide off xhigh still launches
# fine, and the two prose regressions read naturally to anyone who remembers
# the previous rules.

expect_drift_caught 'the worker launch command copies the lead effort' '
p="references/session-launch-guide.md"
s=open(p,encoding="utf-8").read()
q=chr(39)
old="claude --model "+q+"claude-opus-5[1m]"+q+" --effort high --dangerously-skip-permissions"
assert old in s, "worker command not found; fixture is testing nothing"
n=s.replace(old, old.replace("high","max"))
open(p,"w",encoding="utf-8").write(n)'

expect_drift_caught 'the 4.8 implementation tier slides off xhigh' '
p="references/session-launch-guide.md"
s=open(p,encoding="utf-8").read()
q=chr(39)
old="claude --model "+q+"claude-opus-4-8[1m]"+q+" --effort xhigh --dangerously-skip-permissions"
assert old in s, "impl backup command not found; fixture is testing nothing"
n=s.replace(old, old.replace("xhigh","high"))
open(p,"w",encoding="utf-8").write(n)'

expect_drift_caught 'a teammate effort drifts up to max' '
p="assets/claude/agents/orca-opus-reviewer.md"
s=open(p,encoding="utf-8").read()
n=s.replace("effort: high","effort: max")
assert n!=s
open(p,"w",encoding="utf-8").write(n)'

expect_drift_caught 'the quota trigger for Opus 4.8 comes back' '
open("references/routing-policy.md","a",encoding="utf-8").write("\nOpus 4.8只在需要3个以上并行只读队友且Opus 5额度吃紧时使用。\n")'

expect_drift_caught 'implementation collapses back onto Codex' '
open("references/routing-policy.md","a",encoding="utf-8").write("\n判断型工作用Opus 5，实施型工作用Codex。\n")'

# The mirror image of the check above, and the reason it needs one. The drift
# record deliberately cites a superseded version, wrapped in an allow block. If
# the stray scan stopped honouring that block the tree would fail while being
# correct, and the cheapest way to a green run would be deleting the evidence.
( cd "$SCRIPT_DIR/.." && ./scripts/check-consistency.sh >/dev/null 2>&1 ) || {
  printf 'clean tree fails the consistency check; the allow block around the version drift record is not being honoured\n' >&2
  exit 1
}

printf 'orca-task-router fixtures passed\n'
