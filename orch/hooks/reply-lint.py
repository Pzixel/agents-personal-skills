#!/usr/bin/env python3
"""Stop hook (main session): the final reply carries its observation and stays
inside the agent's authority.

Returned once, with the rule that failed, when the reply claims completion
without an observation (command, value, time) or hands a decision to the owner
in words ("your call", "жду вашего решения"), or promises future work in the
first person ("I'll fix it in a follow-up PR") without doing it or naming a
durable handoff (issue, PR, handoff file); a trailing question mark alone
is not a hand-off, answers to the owner's questions end on one. The second stop (`stop_hook_active`) always passes, so a
reply that genuinely needs the owner costs one extra turn, never a loop.
Not claims or hand-offs (2026-10-15): Russian not-verified disclosures, done
words as section labels or with a manner complement, permission requests.
Only payloads whose transcript_path is a file are logged, so probes stay out.
The reason asks for an addendum with only the missing part, never a repeat
of the whole reply.
Decision: gate_policy.reply_lint_decision. Any error exits 0 with no output.

Why a hook: the rules "every done carries proof" and "do not end with owner
decisions" were loaded in the reviewed sessions and not applied at the moment
of replying (retro 2026-10-12: 158 claims without observation, ~30 owned
decisions handed to the owner, about 170 h).
"""
import datetime
import json
import os
import sys

_PLUGIN_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
TOOLS_DIR = (
    _PLUGIN_TOOLS if os.path.isdir(_PLUGIN_TOOLS) else os.path.expanduser("~/.claude/tools")
)
sys.path.insert(0, TOOLS_DIR)

import gate_policy  # noqa: E402

LOG_PATH = os.path.expanduser("~/.claude/reply-lint-log.jsonl")


def last_assistant_text(transcript_path):
    """Last non-empty assistant text of a transcript (payload copy wins when present)."""
    last = ""
    with open(transcript_path, errors="replace") as fh:
        for line in fh:
            if not line.startswith("{"):
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if entry.get("type") != "assistant":
                continue
            content = (entry.get("message") or {}).get("content")
            if isinstance(content, str):
                texts = [content]
            elif isinstance(content, list):
                texts = [str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            else:
                texts = []
            if any(t.strip() for t in texts):
                last = "\n".join(texts)
    return last


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
    # Subagents have their own SubagentStop gates; this one is for the session that talks to the owner.
    if payload.get("agent_type") or payload.get("agent_id"):
        return 0
    text = payload.get("last_assistant_message")
    if not isinstance(text, str) or not text.strip():
        transcript = payload.get("transcript_path") or ""
        if not transcript or not os.path.isfile(transcript):
            return 0
        text = last_assistant_text(transcript)
    stop_hook_active = bool(payload.get("stop_hook_active"))
    decision = gate_policy.reply_lint_decision(text, stop_hook_active)
    if os.path.isfile(payload.get("transcript_path") or ""):
        log(
            {
                "session_id": payload.get("session_id") or "",
                "stop_hook_active": stop_hook_active,
                "decision": decision["decision"],
                "tail": text.strip()[-120:],
            }
        )
    if decision["decision"] == "block":
        json.dump({"decision": "block", "reason": decision["reason"]}, sys.stdout)
    return 0


try:
    main()
except BaseException:
    pass
sys.exit(0)
