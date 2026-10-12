#!/usr/bin/env python3
"""Reads a Bash command on stdin. Exits 0 iff it is nothing but a single,
argument-light invocation of handoff.sh — no chaining, no substitution, no
heredoc (the handoff content goes through Write instead, so there is
nothing here worth smuggling a payload into). Exits 1 otherwise.

Used only at the hard turn cap, where every other read/write tool is denied:
this is the sole gate deciding whether a Bash call is the sanctioned
finishing move or an attempt to keep exploring/editing through the shell.
The single optional argument is restricted to [A-Za-z0-9_-], so there is no
quoting for a stressed agent to get wrong — this is deliberately the
easiest possible command to reproduce correctly under pressure.
"""
import re
import sys

SLUG = r"[A-Za-z0-9_-]+"
CALL = re.compile(
    r"^(cd\s+[A-Za-z0-9_./-]+\s*&&\s*)?"
    r"(bash\s+|sh\s+)?"
    r"(~/\.claude/hooks/handoff\.sh|/Users/[A-Za-z0-9_.-]+/\.claude/hooks/handoff\.sh)"
    r"(\s+(\"" + SLUG + r"\"|'" + SLUG + r"'|" + SLUG + r"))?\s*$"
)


def main() -> int:
    command = sys.stdin.read()
    if "\n" in command.strip():
        return 1  # no heredoc / multi-line form accepted anymore
    return 0 if CALL.match(command.rstrip()) else 1


if __name__ == "__main__":
    sys.exit(main())
