#!/usr/bin/env python3
"""PreToolUse hook for Bash: a change lands on a shared branch only with the
real-path check named in the command.

`gh pr merge` and `git push` to master/main are refused unless the command
carries `CHECK_RAN: <what ran on the real path, result>`, `PROD_POSITION:
<blast-radius dimension, production's number, source>` and `WAY_BACK: <undo,
minutes>`. The tokens are the agent's own statements; the gate only makes
them unavoidable at the moment of merging. Decision: gate_policy.merge_gate_decision. Any error exits 0.
"""
import json
import os
import sys

_PLUGIN_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
TOOLS_DIR = (
    _PLUGIN_TOOLS if os.path.isdir(_PLUGIN_TOOLS) else os.path.expanduser("~/.claude/tools")
)
sys.path.insert(0, TOOLS_DIR)

import gate_policy  # noqa: E402


def main():
    payload = json.load(sys.stdin)
    command = str((payload.get("tool_input") or {}).get("command") or "")
    decision = gate_policy.merge_gate_decision(command)
    if decision["decision"] == "deny":
        print(decision["reason"], file=sys.stderr)
        return 2
    return 0


try:
    sys.exit(main())
except SystemExit:
    raise
except BaseException:
    sys.exit(0)
