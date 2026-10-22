#!/usr/bin/env python3
"""PreToolUse hook: bound the output a code-search agent can pull in.

Active when CLAUDE_SCOUT=1 is in the environment (scout.py sets it for its
agentic finder stage) or when the hook payload says the calling agent is
`orch:scout`. Clamps Grep head_limit to 40 and Read limit to 150 by rewriting the
tool input; a smaller value the agent chose itself is left alone. Anything
else, and any error at all, exits 0 silently: the hook must never block a tool
call.

Measured 2026-09-18 on 58 held-out code-search questions: the clamp took Opus
scout from $0.0627 to $0.0329 per question and accepted-wrong from 9 to 6.
"""
import json
import os
import sys

_PLUGIN_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
sys.path.insert(
    0,
    _PLUGIN_TOOLS if os.path.isdir(_PLUGIN_TOOLS) else os.path.expanduser("~/.claude/tools"),
)

import gate_policy  # noqa: E402

GREP_HEAD_LIMIT = 40
READ_LIMIT = 150
SCOUT_AGENT = "scout"
FIELDS = {"Grep": ("head_limit", GREP_HEAD_LIMIT), "Read": ("limit", READ_LIMIT)}


def decide(scout_env, agent_type, tool_name, tool_input):
    """Pure policy: the updated tool input, or None when nothing changes.

    scout_env  -- True when CLAUDE_SCOUT=1 is set.
    agent_type -- the agent_type field of the hook payload, or None.
    """
    if not scout_env and gate_policy.agent_type_name(agent_type) != SCOUT_AGENT:
        return None
    if not isinstance(tool_input, dict):
        return None
    field_cap = FIELDS.get(tool_name)
    if field_cap is None:
        return None
    field, cap = field_cap
    current = tool_input.get(field)
    try:
        wanted = cap if current is None else min(int(current), cap)
    except (TypeError, ValueError):
        wanted = cap
    if current == wanted:
        return None
    updated = dict(tool_input)
    updated[field] = wanted
    return updated


def main():
    payload = json.loads(sys.stdin.read())
    updated = decide(
        os.environ.get("CLAUDE_SCOUT") == "1",
        payload.get("agent_type"),
        payload.get("tool_name"),
        payload.get("tool_input") or {},
    )
    if updated is None:
        return
    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "updatedInput": updated,
                }
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:  # fail open: never block a tool call
        pass
    sys.exit(0)
