#!/usr/bin/env python3
"""PreToolUse hook (matcher `Bash`): while an offline experiment runs, no agent
of that session may change or reach production.

A session is offline when ~/.claude/offline-sessions.json names it and the
entry has not expired. The session id is the same value for the main session
and for every subagent it spawns (verified on real PreToolUse stdin), so naming
the session once covers the whole tree, whatever any repository's AGENTS.md
authorises.

The decision is gate_policy.offline_decision; the parameters live in
tools/orchestrate-params.json under `offline`. This file only reads the lock,
calls the decision, logs a denial and emits the response. Any error exits 0:
a missing, unreadable or expired lock leaves every command allowed.
"""

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
sys.path.insert(0, TOOLS_DIR)

import gate_policy  # noqa: E402

PARAMS_PATH = os.path.join(TOOLS_DIR, "orchestrate-params.json")
LOCK_PATH = os.environ.get("OFFLINE_LOCK_PATH") or os.path.join(
    STATE_DIR, "offline-sessions.json"
)
LOG_PATH = os.path.join(STATE_DIR, "offline-gate-log.jsonl")


def load_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return {}


def account_of(transcript_path):
    configured = os.environ.get("CLAUDE_CONFIG_DIR", "")
    if configured:
        return "work" if configured.rstrip("/").endswith("/work") else "default"
    if "/.claude-accounts/work/" in (transcript_path or ""):
        return "work"
    return "default"


def session_is_offline(session_id):
    if not session_id:
        return False
    entry = (load_json(LOCK_PATH).get("sessions") or {}).get(session_id)
    if not isinstance(entry, dict):
        return False
    until = str(entry.get("until") or "")[:10]
    if not until:
        return True
    try:
        deadline = datetime.date.fromisoformat(until)
    except ValueError:
        return False
    return datetime.date.today() <= deadline


def log(record):
    try:
        record["ts"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with open(LOG_PATH, "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except BaseException:
        pass


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    payload = json.loads(raw)
    if not session_is_offline(payload.get("session_id") or ""):
        return 0

    command = str((payload.get("tool_input") or {}).get("command") or "")
    decision = gate_policy.offline_decision(command, load_json(PARAMS_PATH))
    if decision["decision"] != "deny":
        return 0

    log(
        {
            "account": account_of(payload.get("transcript_path") or ""),
            "session_id": payload.get("session_id") or "",
            "agent_type": payload.get("agent_type") or "",
            "head": decision.get("head") or "",
            "decision": "deny",
        }
    )
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": decision["reason"],
            }
        },
        sys.stdout,
    )
    return 0


try:
    main()
except BaseException:
    pass
sys.exit(0)
