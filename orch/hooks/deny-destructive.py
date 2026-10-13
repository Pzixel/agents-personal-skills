#!/usr/bin/env python3
"""PreToolUse hook (matcher `Bash`): class destructive commands and answer
allow / ask / deny through the native permission decision.

The decision is gate_policy.destructive_decision; the class table lives in
tools/orchestrate-params.json under `destructive` (its `_doc` says what each
class means). This file only reads stdin, reads the plan file a
DESTRUCTIVE_PLAN marker names, calls the decision, logs every classed
decision to ~/.claude/destructive-log.jsonl and prints the response. It fails
closed: any error is a deny. The agents' kubeconfig is cluster-admin today, so
the `impossible` class is enforced only here until RBAC is tightened.
"""

import datetime
import hashlib
import json
import os
import sys

LOG_PATH = os.path.expanduser("~/.claude/destructive-log.jsonl")


def respond(decision, reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }}))


def read_plan(path):
    try:
        with open(os.path.expanduser(path), "rb") as fh:
            data = fh.read()
    except OSError:
        return None, None
    return data.decode("utf-8", "replace"), hashlib.sha256(data).hexdigest()


def read_sql_file(path, limit):
    """Text of a SQL file a client will read; None when unreadable or over limit."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(limit + 1)
    except OSError:
        return None
    return None if len(data) > limit else data.decode("utf-8", "replace")


def main():
    # Plugin layout first (hooks/../tools), the ~/.claude install as fallback.
    tools = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
    sys.path.insert(0, tools if os.path.isdir(tools) else os.path.expanduser("~/.claude/tools"))
    import gate_policy

    payload = json.load(sys.stdin)
    command = str((payload.get("tool_input") or {}).get("command") or "")
    agent_type = str(payload.get("agent_type") or "")
    marker = gate_policy.plan_marker(command)
    plan_text, plan_hash = read_plan(marker[0]) if marker else (None, None)
    params = gate_policy.load_params()
    cwd = str(payload.get("cwd") or os.getcwd())
    limit = int(params.get("destructive", {}).get("command_max_bytes", 65536))
    sql_file_texts = {
        p: read_sql_file(os.path.join(cwd, os.path.expanduser(p)), limit)
        for p in gate_policy.sql_file_paths(command, params)
    }
    result = gate_policy.destructive_decision(
        command, agent_type, plan_text, plan_hash, params,
        str(payload.get("permission_mode") or ""), sql_file_texts,
    )
    if result["class"] == "none":
        return
    try:
        with open(LOG_PATH, "a") as fh:
            fh.write(json.dumps({
                "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "session_id": payload.get("session_id") or "",
                "agent_type": agent_type,
                "class": result["class"],
                "decision": result["decision"],
                "command": command[:200],
                "plan_hash": plan_hash,
            }) + "\n")
    except OSError:
        pass
    respond(result["decision"], result["reason"])


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        respond("deny", "deny-destructive hook error: %s: %s" % (type(exc).__name__, exc))
    sys.exit(0)
