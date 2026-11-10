#!/usr/bin/env python3
"""PreToolUse gate on AskUserQuestion: the rule, fail-open on a payload without a stem included, is
gate_policy.ask_existing_decision. Input that is not a JSON object passes."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import gate_policy  # noqa: E402


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    reason = gate_policy.ask_existing_decision(payload.get("tool_input") if isinstance(payload, dict) else None)
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
