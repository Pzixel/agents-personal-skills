#!/usr/bin/env python3
"""Claude Code status line: the session's open owner questions (CASE-80), pinned by the
open-question hook. Prints `OPEN QUESTION <q>` or `OPEN QUESTIONS <n>: <q1> | <q2>` cut to one
terminal line; prints nothing when no question is open or anything fails."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))


def render(lines, width):
    if not lines:
        return ""
    head = "OPEN QUESTION " if len(lines) == 1 else "OPEN QUESTIONS %d: " % len(lines)
    text = head + " | ".join(lines)
    return text if len(text) <= width else text[: width - 1] + "…"


def main():
    import gate_policy

    session_id = json.loads(sys.stdin.buffer.read()).get("session_id")
    if not gate_policy.session_id_ok(session_id):
        return
    path = gate_policy.open_question_path(os.path.expanduser("~"), session_id)
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = [line.strip() for line in fh if line.strip()]
    width = int(os.environ.get("COLUMNS") or 200)
    text = render(lines, width)
    if text:
        sys.stdout.buffer.write((text + "\n").encode("utf-8", "replace"))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
