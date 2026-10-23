#!/usr/bin/env python3
"""PreToolUse hook (matcher `Bash|Write|Edit|NotebookEdit`): class destructive commands and answer
allow / ask / deny through the native permission decision.

The decision is gate_policy.destructive_decision; the class table lives in
tools/orchestrate-params.json under `destructive` (its `_doc` says what each
class means). This file only reads stdin, reads the plan file a
DESTRUCTIVE_PLAN marker names, calls the decision, logs every classed
decision of a Claude Code session (UUID session id; hand-piped payloads are
not logged) to ~/.claude/destructive-log.jsonl and prints the response. It fails
closed: any error is a deny. The agents' kubeconfig is cluster-admin today, so
the `impossible` class is enforced only here until RBAC is tightened; an
impossible step runs only from an owner-approved plan (APPROVE <hash>).
File tools are checked only for a path under the approvals dir. An approved
plan run consumes its approval file (written by plan-approval.py) before the
allow is printed; when another PreToolUse hook then denies the same call,
the approval is spent without a run and the owner approves again (fails safe).
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
    params = gate_policy.load_params()
    tool_input = payload.get("tool_input") or {}
    if payload.get("tool_name", "Bash") != "Bash":
        path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        result = gate_policy.approval_path_decision(path, os.path.expanduser("~"), params)
        if result:
            respond(result["decision"], result["reason"])
        return
    command = str((payload.get("tool_input") or {}).get("command") or "")
    agent_type = str(payload.get("agent_type") or "")
    marker = gate_policy.plan_marker(command)
    plan_text, plan_hash = read_plan(marker[0]) if marker else (None, None)
    session = str(payload.get("session_id") or "")
    approval = None
    if marker and gate_policy.session_id_ok(session):
        root = gate_policy.approvals_root(params, os.path.expanduser("~"))
        approval = os.path.join(root, session, marker[1])
    approved = bool(approval) and os.path.isfile(approval)
    cwd = str(payload.get("cwd") or os.getcwd())
    limit = int(params.get("destructive", {}).get("command_max_bytes", 65536))
    sql_file_texts = {
        p: read_sql_file(os.path.join(cwd, os.path.expanduser(p)), limit)
        for p in gate_policy.sql_file_paths(command, params, cwd)
    }
    result = gate_policy.destructive_decision(
        command, agent_type, plan_text, plan_hash, params,
        str(payload.get("permission_mode") or ""), sql_file_texts, approved, cwd,
    )
    if approved and result["decision"] == "allow":
        os.remove(approval)
    if result["class"] == "none":
        return
    if gate_policy.session_id_ok(session):
        try:
            with open(LOG_PATH, "a") as fh:
                fh.write(json.dumps({
                    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "session_id": session,
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
