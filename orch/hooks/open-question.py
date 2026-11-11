#!/usr/bin/env python3
"""Pins the session's open owner questions for the status line (CASE-80): the rule is
gate_policy.open_question_decision. A `QUESTION:` PushNotification appends a line to
~/.claude/open-questions/<session_id>.md; AskUserQuestion, a typed owner prompt and a Stop
with nothing running remove the file. Never blocks, prints nothing, exits 0."""
import json
import os
import sys

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "tools")


def main():
    sys.path.insert(0, TOOLS)
    import gate_policy
    import transcript

    payload = json.load(sys.stdin.buffer)
    path = gate_policy.open_question_path(os.path.expanduser("~"), str(payload.get("session_id")))
    facts = None
    if payload.get("hook_event_name") in ("Stop", "UserPromptSubmit"):
        if not os.path.exists(path):
            return  # nothing to clear: skip the transcript scan
        source = payload.get("transcript_path") or ""
        if source and os.path.isfile(source):
            for facts in transcript.background_watch_scan(transcript.entries_of(source), ""):
                pass
    action, line = gate_policy.open_question_decision(payload, facts)
    if action == "append":
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8", errors="replace") as fh:
            fh.write(line + "\n")
    elif action == "clear" and os.path.exists(path):
        os.remove(path)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
