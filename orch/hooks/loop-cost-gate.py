#!/usr/bin/env python3
"""Loop-cost gate (Bash; main session and every agent).

PostToolUse / PostToolUseFailure: a command that ran at least
``loop_cost.long_command_min_seconds`` is appended to the repository's journal
``~/.claude/loop-cost/<sha1 of the repository root>.jsonl`` (all worktrees and
agents of one repository pool there). PreToolUse: gate_policy.loop_cost_decision
denies another launch of an identity that keeps running long until the command
carries `# LOOP_COST: <reason>`, which is journaled as an acknowledgement.
All decision logic lives in tools/gate_policy.py; this file only moves fields.
Fails open on any error: a broken hook would block every live session."""

import hashlib
import json
import os
import subprocess
import sys
import time

_PLUGIN_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
TOOLS_DIR = (
    _PLUGIN_TOOLS if os.path.isdir(_PLUGIN_TOOLS) else os.path.expanduser("~/.claude/tools")
)
JOURNAL_DIR = os.path.expanduser("~/.claude/loop-cost")


def repository_root(directory):
    try:
        out = subprocess.run(
            ["git", "-C", directory, "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, timeout=3,
        )
        if out.returncode == 0 and out.stdout.strip():
            return os.path.dirname(out.stdout.strip().rstrip("/"))
    except Exception:
        pass
    return directory


def journal_path(gate_policy, command, cwd):
    target = gate_policy.loop_cost_cd_target(command)
    directory = cwd
    if target:
        target = os.path.expanduser(os.path.expandvars(target))
        directory = target if os.path.isabs(target) else os.path.join(cwd, target)
    root = repository_root(directory)
    return os.path.join(JOURNAL_DIR, hashlib.sha1(root.encode()).hexdigest() + ".jsonl")


def read_rows(path):
    rows = []
    try:
        with open(path) as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except FileNotFoundError:
        pass
    return rows


def append_row(path, row):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps(row) + "\n")


def compact(gate_policy, path, now, params):
    rows = read_rows(path)
    live = gate_policy.loop_cost_live(rows, now, params)
    limit = int(gate_policy._merged_params(params)["loop_cost"]["compact_stale_rows"])
    if len(rows) - len(live) <= limit:
        return
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w") as fh:
        fh.writelines(json.dumps(r) + "\n" for r in live)
    os.replace(tmp, path)


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        return
    payload = json.loads(raw)
    if payload.get("tool_name") != "Bash":
        return
    command = (payload.get("tool_input") or {}).get("command") or ""
    cwd = payload.get("cwd") or os.getcwd()
    agent_type = payload.get("agent_type") or ""
    event = payload.get("hook_event_name") or ""

    sys.path.insert(0, TOOLS_DIR)
    import gate_policy

    params = gate_policy.load_params()
    now = time.time()
    path = journal_path(gate_policy, command, cwd)

    if event in ("PostToolUse", "PostToolUseFailure"):
        row = gate_policy.loop_cost_row(command, payload.get("duration_ms"), agent_type, now, params)
        if row:
            append_row(path, row)
            compact(gate_policy, path, now, params)
        return
    if event != "PreToolUse":
        return

    decision = gate_policy.loop_cost_decision(read_rows(path), command, now, agent_type, params)
    if decision["decision"] == "override":
        append_row(path, {
            "ts": gate_policy.loop_cost_iso(now), "duration_ms": 0, "agent_type": agent_type or "main",
            "key": decision["key"], "ack": decision["reason"], "command": command[:200],
        })
    elif decision["decision"] == "deny":
        sys.stdout.write(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": decision["reason"],
        }}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
