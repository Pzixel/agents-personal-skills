#!/usr/bin/env python3
"""PreToolUse hook (Monitor matcher): put the waiting protocol at the call.

The always-on rules (one quiet monitor per subject; do every independent step
now) were loaded and still not applied at a Monitor call: the agent armed a
monitor on every progress line of a job that gated itself, woke ten times in
three minutes, and started no independent goal item. This note restates them
where the decision is made. It never blocks the call."""

import json
import sys

NOTE = (
    "Monitor wakes you once per line it prints. Print only the lines you would act on: "
    "for a job that checks itself and stops on failure, that is its terminal event "
    "(done, FAIL, process gone, timeout); a gate passed or a step finished is a progress "
    "line, and a wake-up on it is noise. If the command prints progress, stop it and "
    "re-arm on the terminal event. While it runs, start in this turn the goal's next item "
    "that does not wait on this job; a quiet monitor with nothing else started is idling."
)

if __name__ == "__main__":
    json.dump(
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": NOTE}},
        sys.stdout,
    )
