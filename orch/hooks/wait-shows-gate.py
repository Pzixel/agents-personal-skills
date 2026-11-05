#!/usr/bin/env python3
"""PreToolUse hook (CronCreate, Monitor): a wait armed an hour or more ahead
as a check names, in a `WAIT_SHOWS:` line, the fact only that event produces
(CASE-68). Policy: gate_policy.wait_shows_decision. Fails open."""

import datetime
import json
import os
import sys

_PLUGIN_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
TOOLS_DIR = (
    _PLUGIN_TOOLS if os.path.isdir(_PLUGIN_TOOLS) else os.path.expanduser("~/.claude/tools")
)


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        return
    payload = json.loads(raw)
    sys.path.insert(0, TOOLS_DIR)
    import gate_policy

    decision = gate_policy.wait_shows_decision(
        payload.get("tool_name") or "",
        payload.get("tool_input") or {},
        datetime.datetime.now().astimezone(),
        gate_policy.load_params(),
    )
    if decision.get("decision") != "deny":
        return
    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": decision.get("reason", ""),
                }
            }
        )
    )


try:
    main()
except BaseException:
    pass
sys.exit(0)
