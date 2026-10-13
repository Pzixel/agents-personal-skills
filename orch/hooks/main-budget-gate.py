#!/usr/bin/env python3
"""PreToolUse hook (matcher Bash|Read|Grep|Glob|Edit|Write|NotebookEdit): throttle hands-on tool calls
in the MAIN session once its context is expensive, so reading gets batched or
delegated instead of paid for at full context.

Subagents are exempt (they are budgeted by agent-turn-budget.sh), which is also
why the subagent-transcript resolution is unnecessary here: when agent_type is
empty, transcript_path is the main session's own transcript.

All decisions live in tools/gate_policy.py. This file reads the transcript TAIL
only (never the whole file -- transcripts reach 100+ MB) and forwards facts.
Fails open on absolutely any error.
"""

import datetime
import json
import os
import sys

TAIL_BYTES = 2_000_000
# Plugin layout first (hooks/../tools), the ~/.claude install as fallback;
# logs and locks stay in ~/.claude whichever copy of the hook runs.
_PLUGIN_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
TOOLS_DIR = (
    _PLUGIN_TOOLS if os.path.isdir(_PLUGIN_TOOLS) else os.path.expanduser("~/.claude/tools")
)
STATE_DIR = os.path.expanduser("~/.claude")
OVERRIDE_LOG = os.path.join(STATE_DIR, "main-budget-overrides.jsonl")
SKILL_LOG = os.path.join(STATE_DIR, "skill-load-log.jsonl")
# A session's skill loads are recent: the last ~1300 records cover them.
SKILL_LOG_TAIL_BYTES = 256_000
# Spawning and bookkeeping tools are not hands-on work; hand edits are.
SKIP_TOOLS = frozenset({"Agent", "TodoWrite", "Task", "SendMessage"})
SPAWN_TOOLS = frozenset({"Agent", "Task"})


def account_of(transcript_path):
    configured = os.environ.get("CLAUDE_CONFIG_DIR", "")
    if configured:
        return "work" if configured.rstrip("/").endswith("/work") else "default"
    if "/.claude-accounts/work/" in (transcript_path or ""):
        return "work"
    return "default"


def tail_lines(path, max_bytes=TAIL_BYTES):
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        start = max(0, size - max_bytes)
        fh.seek(start)
        blob = fh.read()
    if start > 0:
        nl = blob.find(b"\n")
        blob = blob[nl + 1:] if nl >= 0 else b""
    out = []
    for line in blob.decode("utf-8", errors="ignore").splitlines():
        if not line.startswith("{"):
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def target_of(tool_input):
    for key in ("command", "file_path", "path", "pattern"):
        value = tool_input.get(key)
        if value:
            return value
    return ""


def blocks_of(entry):
    message = entry.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def is_user_turn(entry):
    """A real user prompt, not a tool result carried on a user-typed line."""
    if entry.get("type") != "user":
        return False
    message = entry.get("message")
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        return not any(b.get("type") == "tool_result" for b in content if isinstance(b, dict))
    return False


def context_tokens(entries):
    for entry in reversed(entries):
        message = entry.get("message")
        usage = message.get("usage") if isinstance(message, dict) else None
        if isinstance(usage, dict):
            return (
                int(usage.get("input_tokens") or 0)
                + int(usage.get("cache_read_input_tokens") or 0)
                + int(usage.get("cache_creation_input_tokens") or 0)
            )
    return 0


def last_boundary(entries):
    boundary = None
    for i, entry in enumerate(entries):
        if entry.get("isSidechain"):
            continue
        if is_user_turn(entry):
            boundary = i
            continue
        if entry.get("type") == "system" and "agent" in str(entry.get("subtype") or ""):
            boundary = i
            continue
        if entry.get("type") == "assistant":
            for block in blocks_of(entry):
                if block.get("type") == "tool_use" and block.get("name") in SPAWN_TOOLS:
                    boundary = i
    return boundary


def loaded_skills(session_id):
    """Skills this session loaded, from the tail of the skill-load-gate log."""
    if not session_id or not os.path.isfile(SKILL_LOG):
        return []
    return [
        r["skill"]
        for r in tail_lines(SKILL_LOG, SKILL_LOG_TAIL_BYTES)
        if r.get("session_id") == session_id and r.get("skill")
    ]


def hands_on_calls(entries, boundary, classify):
    count = 0
    for entry in entries[boundary + 1:]:
        if entry.get("isSidechain") or entry.get("type") != "assistant":
            continue
        for block in blocks_of(entry):
            if block.get("type") != "tool_use" or block.get("name") in SKIP_TOOLS:
                continue
            target = target_of(block.get("input") or {})
            if classify(block.get("name"), target) in ("exempt", "wait_for"):
                continue
            count += 1
    return count


def log_override(payload, ctx_tokens, window_calls, command, transcript_path, reason):
    try:
        record = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "account": account_of(transcript_path),
            "session_id": payload.get("session_id") or "",
            "tool_name": payload.get("tool_name") or "",
            "ctx_tokens": ctx_tokens,
            "window_calls": window_calls,
            "reason": reason,
            "command": command[:300],
        }
        with open(OVERRIDE_LOG, "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except BaseException:
        pass


def emit(obj):
    sys.stdout.write(json.dumps({"hookSpecificOutput": obj}))


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        return
    payload = json.loads(raw)
    if payload.get("agent_type"):
        return

    transcript_path = payload.get("transcript_path") or ""
    if not transcript_path or not os.path.isfile(transcript_path):
        return
    cmd_or_path = target_of(payload.get("tool_input") or {})

    sys.path.insert(0, TOOLS_DIR)
    import gate_policy

    entries = tail_lines(transcript_path)
    boundary = last_boundary(entries)
    if boundary is None:
        return
    ctx_tokens = context_tokens(entries)
    params = gate_policy.load_params()
    window_calls = hands_on_calls(
        entries, boundary, lambda tool, target: gate_policy.tool_call_class(tool, target, params)
    )

    decision = gate_policy.budget_decision(
        "", ctx_tokens, window_calls, cmd_or_path,
        tool_name=payload.get("tool_name") or "",
        loaded_skills=loaded_skills(payload.get("session_id") or ""),
        params=params,
    )
    verdict = decision.get("decision")
    if verdict == "deny":
        emit(
            {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": decision.get("reason", ""),
            }
        )
    elif verdict == "remind":
        emit({"hookEventName": "PreToolUse", "additionalContext": decision.get("message", "")})
    elif verdict == "override":
        log_override(payload, ctx_tokens, window_calls, cmd_or_path, transcript_path, decision["reason"])


try:
    main()
except BaseException:
    pass
sys.exit(0)
