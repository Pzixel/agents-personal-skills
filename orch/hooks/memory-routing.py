#!/usr/bin/env python3
"""PostToolUse hook: when a user/feedback memory is written into project memory,
remind the agent that user-level rules belong in ~/.claude/user-rules.md."""
import json
import re
import sys

event = json.load(sys.stdin)
path = event.get("tool_input", {}).get("file_path", "")
if "/memory/" not in path or not path.endswith(".md") or path.endswith("MEMORY.md"):
    sys.exit(0)
try:
    text = open(path, encoding="utf-8").read()
except OSError:
    sys.exit(0)
if not re.search(r"^\s*type:\s*(user|feedback)\b", text, re.M):
    sys.exit(0)

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": (
            "You saved a user/feedback memory into project memory. If the rule is true "
            "outside this repository, move it: add or update one line in "
            "~/.claude/user-rules.md, delete this file and its MEMORY.md line, and tell "
            "the user where it went. If it is repository-specific, keep it here."
        ),
    }
}))
