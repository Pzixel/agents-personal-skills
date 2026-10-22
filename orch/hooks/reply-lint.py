#!/usr/bin/env python3
"""Stop hook (main session): the final reply carries its observation, does
not defer work it could do now, and does not leave the owner's input in plain text.

Returned once, with the rules that failed, when the reply claims completion
without an observation (command, value, time; a regex), or a small-model judge
finds either an owner hand-off (work cannot continue now until the owner's
choice, answer, approval or input, asked in plain text instead of
AskUserQuestion, or a pointer to an earlier message) or an unbacked deferral
(work the agent could do this turn, waiting on nothing, tracked nowhere). The
judge is called only when gate_policy.needs_judge holds (the reply addresses
the owner or holds a first-person commitment): `claude -p --model haiku` sees
only the final reply (gate_policy.judge_prompt), runs with no hooks, plugins,
MCP or settings, and is guarded by REPLY_LINT_JUDGE against recursion. The
second stop (`stop_hook_active`) passes without a model call, so a flagged
reply costs one extra turn, never a loop. A judge error, timeout or
unparsable verdict passes the reply (fail open) and is logged; every record
names the rules that fired. Measurement and acceptance condition: ORIGINS.md.
Not claims (2026-10-15): Russian not-verified disclosures, done words as
section labels, with a manner complement or followed by a negation.
Only payloads whose transcript_path is a file are logged, so probes stay out.
The reason asks for an addendum with only the missing part, never a repeat
of the whole reply.
A reply whose prose (no code, URLs or table lines) holds no letter of the
chat-reply script and 40+ letters of another one is returned once with the rule
`language` (retro 2026-10-22, CASE-43: hook templates and rule text in English
pulled three sessions into English for 86-100% of their replies; the rule line
alone did not hold). Harness texts in the assistant slot (`API Error:`, limit
notices) are skipped.
Decision: gate_policy.reply_lint_decision. Any error exits 0 with no output.

Why a hook: the rule "every done carries proof" was loaded in the reviewed sessions and not applied at the moment
of replying (retro 2026-10-12: 158 claims without observation, ~30 owned
decisions handed to the owner, about 170 h).
"""
import datetime
import json
import os
import subprocess
import sys
import tempfile

_PLUGIN_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
TOOLS_DIR = (
    _PLUGIN_TOOLS if os.path.isdir(_PLUGIN_TOOLS) else os.path.expanduser("~/.claude/tools")
)
sys.path.insert(0, TOOLS_DIR)

import gate_policy  # noqa: E402

LOG_PATH = os.path.expanduser("~/.claude/reply-lint-log.jsonl")
# The chat-reply language of the owner's rules (~/.codex/AGENTS.md "Chat replies in
# Russian"); this hook is that line's enforcement, so the script lives here too.
REPLY_SCRIPT = gate_policy.CYRILLIC
JUDGE_ENV = "REPLY_LINT_JUDGE"
# Under the hook's 15 s timeout; judge wall time p90 is in ORIGINS.md.
JUDGE_TIMEOUT_S = 12
JUDGE_ARGS = [
    "claude", "-p", "--model", "haiku", "--tools", "", "--no-session-persistence",
    "--setting-sources", "", "--settings", '{"disableAllHooks":true}', "--strict-mcp-config",
    "--disable-slash-commands", "--output-format", "json",
    "--system-prompt", "You classify one reply. Respond with JSON only.",
]


def judge(text):
    """(verdict or None, error or None) from the nested small-model judge."""
    try:
        run = subprocess.run(
            JUDGE_ARGS + [gate_policy.judge_prompt(text)],
            capture_output=True, text=True, timeout=JUDGE_TIMEOUT_S,
            cwd=tempfile.gettempdir(), env=dict(os.environ, **{JUDGE_ENV: "1"}),
        )
    except Exception as exc:
        return None, type(exc).__name__
    if run.returncode != 0:
        return None, "exit %d" % run.returncode
    verdict = gate_policy.parse_judge_verdict(run.stdout)
    return verdict, None if verdict else "unparsable: " + run.stdout[-200:]


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
    if not raw.strip() or os.environ.get(JUDGE_ENV):
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
    skip_judge = stop_hook_active or not gate_policy.needs_judge(text)
    verdict, judge_error = (None, None) if skip_judge else judge(text)
    decision = gate_policy.reply_lint_decision(text, stop_hook_active, verdict, REPLY_SCRIPT)
    if os.path.isfile(payload.get("transcript_path") or ""):
        log(
            {
                "session_id": payload.get("session_id") or "",
                "stop_hook_active": stop_hook_active,
                "decision": decision["decision"],
                "rules": decision["rules"],
                "verdict": verdict,
                "judge_error": judge_error,
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
