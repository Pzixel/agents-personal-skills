#!/usr/bin/env python3
"""PreToolUse hook (matcher `Skill`): keep large expert skills out of the MAIN
session's context.

Expertise belongs where the work is done: a work agent loads the skill it needs
and pays for it for the length of its own life, while the same text in the main
session is re-sent every turn for the rest of the session. Subagents are never
touched by this hook (`agent_type` is present inside a subagent and absent in
the main session).

The decision is gate_policy.skill_load_decision; thresholds and the list of
process skills live in tools/orchestrate-params.json. This file only resolves
the skill directory, measures it (SKILL.md on load; SKILL.md plus every *.md
as the documents an agent may read; scripts are executed, not counted), reads
the override, and emits the response.
Any error at all exits 0: the gate never stands between an agent and its work
because something was unreadable.
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
LOG_PATH = os.path.join(STATE_DIR, "skill-load-log.jsonl")
TAIL_BYTES = 2_000_000
MESSAGES_SCANNED = 8

def load_params():
    try:
        with open(PARAMS_PATH) as fh:
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


def skill_dir(skill_name, cwd):
    """First existing directory for this skill across the config, user and
    project scopes, with symlinks resolved."""
    candidates = []
    configured = os.environ.get("CLAUDE_CONFIG_DIR", "")
    if configured:
        candidates.append(os.path.join(configured, "skills", skill_name))
    candidates.append(os.path.expanduser("~/.claude/skills/%s" % skill_name))
    if cwd:
        candidates.append(os.path.join(cwd, ".claude", "skills", skill_name))
    for candidate in candidates:
        real = os.path.realpath(os.path.expanduser(candidate))
        if os.path.isdir(real):
            return real
    return ""


def skill_sizes(directory):
    """(on-load bytes, docs bytes): SKILL.md is what the harness injects; the
    docs are SKILL.md plus every other *.md under the directory."""
    on_load = os.path.getsize(os.path.join(directory, "SKILL.md"))
    docs = 0
    for root, _dirs, files in os.walk(directory, followlinks=True):
        for name in files:
            if name.lower().endswith(".md"):
                try:
                    docs += os.path.getsize(os.path.join(root, name))
                except OSError:
                    continue
    return on_load, docs


def tail_entries(path):
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        start = max(0, size - TAIL_BYTES)
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


def entry_text(entry):
    message = entry.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "\n".join(parts)


def override_reason(transcript_path, params):
    """The override is read from the latest user/assistant text, the way
    a marker line as gate_policy.override_marker defines it."""
    if not transcript_path or not os.path.isfile(transcript_path):
        return ""
    seen = 0
    for entry in reversed(tail_entries(transcript_path)):
        if entry.get("type") not in ("user", "assistant"):
            continue
        text = entry_text(entry)
        if not text:
            continue
        seen += 1
        reason = gate_policy.override_marker(text, "SKILL_LOAD_OVERRIDE", params)
        if reason:
            return reason
        if seen >= MESSAGES_SCANNED:
            break
    return ""


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
    if payload.get("agent_type"):
        return 0

    tool_input = payload.get("tool_input") or {}
    skill_name = str(tool_input.get("skill") or "").strip()
    if not skill_name:
        return 0

    transcript_path = payload.get("transcript_path") or ""
    directory = "" if ":" in skill_name else skill_dir(skill_name, payload.get("cwd") or os.getcwd())
    if not directory:
        # Plugin or unresolved skill: not measured, allowed, still logged.
        log({
            "account": account_of(transcript_path),
            "session_id": payload.get("session_id") or "",
            "skill": skill_name.split(":")[-1],
            "decision": "allow",
        })
        return 0

    size, docs = skill_sizes(directory)
    params = load_params()
    reason = override_reason(transcript_path, params)
    decision = gate_policy.skill_load_decision(
        True, size, skill_name, params, reason, docs
    )

    record = {
        "account": account_of(transcript_path),
        "session_id": payload.get("session_id") or "",
        "skill": skill_name,
        "skill_bytes": size,
        "docs_bytes": docs,
        "decision": decision["decision"],
    }
    if decision.get("override"):
        record["override_reason"] = decision["override"]
    # Every load is logged: main-budget-gate reads allowed loads to learn
    # which skills this session runs.
    log(record)
    if decision["decision"] == "deny":
        out = {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": decision["reason"],
        }
    elif decision.get("context"):
        out = {"hookEventName": "PreToolUse", "additionalContext": decision["context"]}
    else:
        return 0
    json.dump({"hookSpecificOutput": out}, sys.stdout)
    return 0


try:
    main()
except BaseException:
    pass
sys.exit(0)
