#!/usr/bin/env python3
"""SubagentStop hook (agent type builder): an agent that edited
files does not finish without running the check its assignment declared.

The assignment names the check; the mechanism only verifies that it ran after
the last edit. There is no built-in list of build or test commands here: the
declared check is whatever line starting with `check:` the first user message
of the agent contains.

The agent is returned at most once (a second stop arrives with
`stop_hook_active` and is let through), so a check that genuinely cannot run in
that environment costs one extra turn and a sentence in the report, not a loop.
Decision: gate_policy.checks_decision. Any error exits 0 with no output.
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

LOG_PATH = os.path.join(STATE_DIR, "checks-gate-log.jsonl")
AGENT_TYPES = frozenset({"builder"})
# A file edit is an editing tool call or a Bash command that writes (the write
# side of is_read_call); a to-do list or a spawn is not an edit.
EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
BLOCK_TEXT = (
    "run `{check}` through check-run, or state in your reply why it cannot be run. "
    "Then give your COMPLETE final reply again: only your last message reaches the caller, "
    "so a reply that says 'as reported above' delivers nothing"
)
# What this hook itself said last time: seeing it after the last edit means the
# agent was already returned once for this check.
BLOCK_MARKER = "through check-run, or state in your reply"


def account_of(transcript_path):
    configured = os.environ.get("CLAUDE_CONFIG_DIR", "")
    if configured:
        return "work" if configured.rstrip("/").endswith("/work") else "default"
    if "/.claude-accounts/work/" in (transcript_path or ""):
        return "work"
    return "default"


def entries_of(path):
    out = []
    with open(path, errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def blocks_of(entry):
    message = entry.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def declared_check(entries):
    """The `check:` line of the agent's first user message, backticks stripped."""
    for entry in entries:
        if entry.get("type") != "user":
            continue
        for block in blocks_of(entry):
            if block.get("type") != "text":
                continue
            for line in str(block.get("text") or "").splitlines():
                stripped = line.strip().lstrip("-*").strip()
                if stripped.lower().startswith("check:"):
                    return stripped.split(":", 1)[1].strip().strip("`").strip()
        return ""
    return ""


def events_of(entries):
    """Ordered record of what the agent did: edits, commands run, and the
    reminder this hook already delivered."""
    events = []
    for entry in entries:
        kind = entry.get("type")
        if kind == "user":
            for block in blocks_of(entry):
                text = str(block.get("text") or "") if block.get("type") == "text" else ""
                if BLOCK_MARKER in text:
                    events.append({"kind": "reminder"})
            continue
        if kind != "assistant":
            continue
        for block in blocks_of(entry):
            if block.get("type") != "tool_use":
                continue
            tool = block.get("name") or ""
            tool_input = block.get("input") or {}
            if tool == "Bash":
                command = str(tool_input.get("command") or "")
                if command and not gate_policy.is_read_call(tool, command):
                    events.append({"kind": "edit"})
                if command:
                    events.append({"kind": "command", "command": command})
            elif tool in EDIT_TOOLS:
                events.append(
                    {"kind": "edit", "path": str(tool_input.get("file_path") or "")}
                )
    return events


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
    if payload.get("stop_hook_active"):
        return 0
    agent_type = payload.get("agent_type") or ""
    if gate_policy.agent_type_name(agent_type) not in AGENT_TYPES:
        return 0

    transcript = payload.get("agent_transcript_path") or ""
    if not transcript or not os.path.isfile(transcript):
        return 0

    entries = entries_of(transcript)
    check = declared_check(entries)
    decision = gate_policy.checks_decision(check, events_of(entries))

    record = {
        "account": account_of(transcript),
        "agent_type": agent_type,
        "agent_id": payload.get("agent_id") or "",
        "session_id": payload.get("session_id") or "",
        "check": check,
        "decision": "undeclared" if decision.get("undeclared") else decision["decision"],
    }
    log(record)

    if decision["decision"] == "return":
        json.dump(
            {"decision": "block", "reason": BLOCK_TEXT.format(check=check)}, sys.stdout
        )
    return 0


try:
    main()
except BaseException:
    pass
sys.exit(0)
