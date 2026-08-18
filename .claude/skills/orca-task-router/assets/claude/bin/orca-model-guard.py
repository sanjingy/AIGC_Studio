#!/usr/bin/env python3
"""Deny any Agent spawn that pins a model through the tool's `model` parameter.

Input : a PreToolUse hook payload on stdin, registered on matcher "Agent".
Output: a hookSpecificOutput JSON with permissionDecision "deny", or nothing.
Exit   : always 0. Exit 2 would also block, but then the JSON is ignored and the
         operator loses the remediation text that makes the model self-correct.

Why deny every value rather than allow-list the good ones: the tool's schema
accepts exactly four aliases -- sonnet, opus, haiku, fable -- and rejects full
model IDs with InputValidationError before a hook ever runs. So the allow-list
this Skill actually wants (claude-opus-5[1m] and friends) is unexpressible here.
What is left are aliases, and an alias carries no [1m], which violates the
"every session gets 1M context" hard constraint on its own.

Aliases also drift. On Claude Code 2.1.217 `opus` resolved to Opus 4.8; on
2.1.222 the same alias resolves to claude-opus-5[1m]. Omitting the parameter
does not drift: the subagent inherits the parent session's model, suffix
included. Verified 2026-08-05 -- a general-purpose subagent spawned with no
`model` reported `claude-opus-5[1m]` and modelUsage showed contextWindow
1000000.

This guard fails open. A crash here must not wedge every dispatch on the box;
losing the guard costs one mis-tiered Worker, fail-closed costs the session.
"""

import json
import sys

REASON = (
    "[Orca档位守卫] 这次Agent调用传了 model='{model}'，已拒绝。\n"
    "Agent工具的 model 参数只接受 sonnet/opus/haiku/fable 四个别名，"
    "完整模型名（claude-opus-5[1m] 这种）会被schema直接拒掉，所以这个参数"
    "无法表达团队档位。别名还拿不到 [1m]，违反“所有子session显式1M上下文”。\n"
    "按下面三条改：\n"
    "1. 判断型只读工作：删掉 model 参数重发。不传时子Agent继承本会话的"
    "claude-opus-5[1m]，含1M上下文，这正是要的档位。\n"
    "2. 要指定非默认档位：改用锁了模型的预设队友，subagent_type 选 "
    "orca-opus-reviewer（只读Review）、orca-opus-investigator（要跑命令）或 "
    "orca-fable-architect（过了Fable闸门的长期自主调查）。档位写在它们的"
    "定义文件里，不经过这个参数。\n"
    "3. 要改文件、要失败重试计数和可审计返工血缘：不要用Agent工具，"
    "开Codex Terminal Worker，命令见 orca-task-router 的"
    " references/session-launch-guide.md。\n"
    "档位选择规则见该Skill，不要自行降档省token。"
)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return  # Unparseable payload: cannot judge, so do not block.

    if not isinstance(payload, dict):
        return

    # The matcher is a regex, so any tool whose name contains "Agent" reaches
    # this hook; denying those would be collateral damage. Checking the name
    # does not protect against a future rename -- if the tool is renamed the
    # matcher stops matching and this script is never invoked at all, which is
    # exactly how a guard dies silently. That failure mode is covered by the
    # manual re-verification SKILL.md requires after a Claude Code upgrade.
    if payload.get("tool_name") != "Agent":
        return

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return

    # Absent parameter is the correct call: inheritance or the agent
    # definition supplies the tier. Only an explicit pin is a violation.
    if "model" not in tool_input:
        return

    model = tool_input.get("model")
    if model is None:
        return

    json.dump({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": REASON.format(model=model),
        }
    }, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # Never wedge a dispatch over a guard failure.
    sys.exit(0)
