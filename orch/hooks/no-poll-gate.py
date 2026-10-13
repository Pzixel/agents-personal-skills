#!/usr/bin/env python3
"""PreToolUse hook (Bash matcher, main session and all agents): refuse
foreground polling loops, and any run_in_background call from a subagent. All decision logic lives in tools/gate_policy.py;
this file only moves fields. Escape hatch: a line `NO_POLL_OVERRIDE: <reason>` in the
command. Fails open on absolutely any error -- a broken hook would block every
live session."""

import datetime
import json
import os
import sys

# Plugin layout first (hooks/../tools), the ~/.claude install as fallback;
# logs and locks stay in ~/.claude whichever copy of the hook runs.
_PLUGIN_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
TOOLS_DIR = (
    _PLUGIN_TOOLS if os.path.isdir(_PLUGIN_TOOLS) else os.path.expanduser("~/.claude/tools")
)
STATE_DIR = os.path.expanduser("~/.claude")
OVERRIDE_LOG = os.path.join(STATE_DIR, "no-poll-overrides.jsonl")


def account_of(transcript_path):
    configured = os.environ.get("CLAUDE_CONFIG_DIR", "")
    if configured:
        return "work" if configured.rstrip("/").endswith("/work") else "default"
    if "/.claude-accounts/work/" in (transcript_path or ""):
        return "work"
    return "default"


def log_override(payload, command, reason):
    try:
        record = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "account": account_of(payload.get("transcript_path") or ""),
            "agent_type": payload.get("agent_type") or "",
            "reason": reason,
            "command": command[:200],
        }
        with open(OVERRIDE_LOG, "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except BaseException:
        pass


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        return
    payload = json.loads(raw)
    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command") or ""
    background = bool(
        tool_input.get("run_in_background") or payload.get("run_in_background")
    )

    sys.path.insert(0, TOOLS_DIR)
    import gate_policy

    params = gate_policy.load_params()
    decision = gate_policy.poll_decision(
        command,
        background,
        payload.get("agent_type") or "",
        params,
        tool_input.get("timeout"),
    )
    if decision.get("decision") == "override":
        log_override(payload, command, decision["reason"])
        return
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
