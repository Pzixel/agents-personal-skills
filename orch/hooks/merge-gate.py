#!/usr/bin/env python3
"""PreToolUse hook for Bash: a change lands on a shared branch only with the
real-path check named in the command.

`gh pr merge` and `git push` to master/main are refused unless the command
carries `CHECK_RAN: <what ran on the real path, result>`, `PROD_POSITION:
<blast-radius dimension, production's number, source>` and `WAY_BACK: <undo,
minutes>`, and `ALWAYS_ON:` when the pushed range changes an always-on
rules file. The tokens are the agent's own statements; the gate only makes
them unavoidable at the moment of merging. Decision: gate_policy.merge_gate_decision. Any error exits 0.
"""
import json
import os
import subprocess
import sys

_PLUGIN_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
TOOLS_DIR = (
    _PLUGIN_TOOLS if os.path.isdir(_PLUGIN_TOOLS) else os.path.expanduser("~/.claude/tools")
)
sys.path.insert(0, TOOLS_DIR)

import gate_policy  # noqa: E402


def _pushed_paths(command, cwd):
    """Paths the pushed range changes, or None when unknown (fail open)."""
    rng = gate_policy.push_range(command, cwd)
    if rng is None:
        return None
    try:
        out = subprocess.run(
            ["git", "-C", rng["dir"], "diff", "--name-only",
             "%s/%s...%s" % (rng["remote"], rng["target"], rng["src"])],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.split() if out.returncode == 0 else None


def main():
    payload = json.load(sys.stdin)
    command = str((payload.get("tool_input") or {}).get("command") or "")
    decision = gate_policy.merge_gate_decision(command, _pushed_paths(command, payload.get("cwd")))
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
