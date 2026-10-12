#!/usr/bin/env python3
"""PreToolUse hook for Bash: refuse commands that would raise a permission prompt.

Unattended runs (a /goal, a /loop, a subagent) stall on a permission prompt
that nobody can answer. This hook turns the prompt into an immediate refusal
with an instruction, so the agent takes the alternative and keeps working.

Override: include `DESTRUCTIVE_OK: <reason>` in the command. Use it only on the
owner's explicit instruction in the main session.
"""
import json
import re
import sys

PATTERNS = [
    (r"(^|[\s;&|(])rm(\s|$)", "rm in any form: leave the file or overwrite it with Write"),
    (r"(^|[\s;&|(])(sudo|apt-get|apt|brew\s+uninstall)(\s|$)", "privileged package or system commands"),
    (r"(^|[\s;&|(])(kill|pkill|killall)(\s|$)", "kill/pkill: stop a job with its own tool or let it finish"),
    (r"(^|[\s;&|(])(chmod|chown)(\s|$)", "chmod/chown: file modes are not changed by agents"),
    (r"(^|[\s;&|(])(sh|bash|zsh)\s+-l?c(\s|$)", "shell -c wrappers: run the command directly"),
    (r"\bgit\s+(clean|reset\s+--hard|checkout\s+--\s|push\s+[^|;]*(-f|--force)(\s|$))",
     "destructive git: use a new commit, a branch, or a worktree instead"),
    (r"\bkubectl\s+delete\s+(?!.*-l\s)(?!.*--selector)", "kubectl delete without a label selector: clean k8s objects by label"),
]

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

command = (payload.get("tool_input") or {}).get("command") or ""
if "DESTRUCTIVE_OK:" in command:
    sys.exit(0)

for pattern, advice in PATTERNS:
    if re.search(pattern, command):
        print(
            "Refused by deny-destructive hook: this command would need a permission prompt "
            f"({advice}). Do the alternative, continue the task, and list the skipped step in the reply. "
            "Main session on the owner's explicit instruction only: add `DESTRUCTIVE_OK: <reason>` to the command.",
            file=sys.stderr,
        )
        sys.exit(2)

sys.exit(0)
