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
impossible step runs only from a plan.
A plan the form accepts runs at once (class `plan`, owner `not_needed`, the
report instruction in additionalContext) unless gate_policy.plan_requirement
says the owner approves it: a pin line, a `# irreversible:` value other than
`none`, or the floor (an impossible or privileged hit of the classifier over
the plan text, a quote it cannot pair, a reading over its work budget, or a
line that drops or truncates a SQL object). A plan log row carries plan_path, cwd, owner (not_needed, requested,
approved, form_refused) and reason; a not_needed row with an approval file
present carries stale_approval: true (the file stays for a later run).
When the destructive decision allows, the headroom check (tools/headroom_read.py)
runs in the same process for every `kubectl exec`; it fails open.
File tools are checked only for a path under the approvals dir. An approved
owner-needed plan run consumes its approval file (written by plan-approval.py) before the
allow is printed; when another PreToolUse hook then denies the same call,
the approval is spent without a run and the owner approves again (fails safe).
"""

import datetime
import hashlib
import json
import os
import sys

LOG_PATH = os.path.expanduser("~/.claude/destructive-log.jsonl")


def respond(decision, reason, context=None):
    out = {"hookEventName": "PreToolUse"}
    if decision:
        out.update(permissionDecision=decision, permissionDecisionReason=reason)
    if context:
        out["additionalContext"] = context
    print(json.dumps({"hookSpecificOutput": out}))


def headroom(command, params, targets):
    """The headroom check (tools/headroom_read.py) over the exec targets the
    destructive walk found; it fails open: a broken read or parse never
    blocks a Bash call, unlike the destructive class."""
    try:
        import headroom_read
        return headroom_read.headroom_check(command, params, targets)
    except Exception as exc:
        import gate_policy
        return gate_policy.headroom_error(exc)


def read_plan(path):
    try:
        with open(os.path.expanduser(path), "rb") as fh:
            data = fh.read()
    except OSError:
        return None, None
    return data.decode("utf-8", "replace"), hashlib.sha256(data).hexdigest()


def observe_pins(gate_policy, plan_text):
    """Facts for the files the plan pins and runs (tools/pin_read.py); no pin, no git call."""
    paths = gate_policy.plan_pinned_paths(plan_text)
    if not paths:
        return {}
    import pin_read
    return pin_read.observe(paths)


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
            reason = result["reason"]
            if result["decision"] == "deny":
                reason += gate_policy._NO_WORKAROUND
            respond(result["decision"], reason)
        return
    command = str((payload.get("tool_input") or {}).get("command") or "")
    agent_type = str(payload.get("agent_type") or "")
    marker = gate_policy.plan_marker(command)
    plan_text, plan_hash = read_plan(marker[0]) if marker else (None, None)
    pin_facts = observe_pins(gate_policy, plan_text)
    session = str(payload.get("session_id") or "")
    approval = None
    if marker and gate_policy.session_id_ok(session):
        root = gate_policy.approvals_root(params, os.path.expanduser("~"))
        approval = os.path.join(root, session, marker[1])
    approved = bool(approval) and os.path.isfile(approval)
    cwd = str(payload.get("cwd") or os.getcwd())
    limit = int(params.get("destructive", {}).get("command_max_bytes", 65536))

    def log(row):
        if not gate_policy.session_id_ok(session):
            return
        try:
            with open(LOG_PATH, "a") as fh:
                fh.write(json.dumps(dict({
                    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "session_id": session,
                    "agent_type": agent_type,
                    "command": command[:200],
                }, **row)) + "\n")
        except OSError:
            pass

    try:
        paths = gate_policy.sql_file_paths(command, params, cwd)
    except gate_policy.OutOfWork as exc:
        log({"class": "impossible", "decision": "deny", "plan_hash": plan_hash})
        respond("deny", exc.reason)
        return
    sql_file_texts = {p: read_sql_file(os.path.join(cwd, os.path.expanduser(p)), limit) for p in paths}
    result = gate_policy.destructive_decision(
        command, agent_type, plan_text, plan_hash, params,
        str(payload.get("permission_mode") or ""), sql_file_texts, approved, cwd, pin_facts,
    )
    if result.get("owner") == "approved":
        os.remove(approval)
    targets = result.get("exec_targets")
    room = headroom(command, params, targets) if targets and result["decision"] == "allow" else None

    if room and room["decision"] == "deny":
        log({"class": "headroom", "decision": "deny", "reason": room["reason"][:300],
             "target": room.get("target"), "limit": room.get("limit"), "use": room.get("use"),
             "adds": room.get("adds")})
        respond("deny", room["reason"] + gate_policy._NO_WORKAROUND)
        return
    # A plan that ran without the owner tells the agent to report it: an allow's reason reaches nobody.
    context = "\n".join(filter(None, [room and room["context"], result.get("context")])) or None
    if result["class"] == "none":
        if context:
            respond(None, None, context)
        return
    row = {"class": result["class"], "decision": result["decision"], "plan_hash": plan_hash}
    if marker:
        row.update(plan_path=marker[0], cwd=cwd, owner=result.get("owner"), reason=result["reason"][:300])
        if approved and result.get("owner") == "not_needed":
            row["stale_approval"] = True
    log(row)
    respond(result["decision"], result["reason"], context)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        respond("deny", "deny-destructive hook error: %s: %s" % (type(exc).__name__, exc))
    sys.exit(0)
