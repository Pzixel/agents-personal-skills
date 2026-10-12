#!/usr/bin/env python3
"""Print human messages from a Claude Code transcript with line numbers.

Skips tool results, sidechain records, and harness noise (task
notifications, system reminders, local command output). Usage:

    humans.py <transcript.jsonl> [--grep REGEX] [--width N]
"""

from __future__ import annotations

import argparse
import json
import re

NOISE_PREFIXES = ("<task-notification", "<system-reminder", "<local-command")


def human_text(record: dict) -> str | None:
    if record.get("type") != "user" or record.get("isSidechain"):
        return None
    content = record.get("message", {}).get("content")
    if isinstance(content, list):
        if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in content):
            return None
        text = " ".join(x.get("text", "") for x in content if isinstance(x, dict))
    else:
        text = content or ""
    text = text.strip()
    if not text or text.startswith(NOISE_PREFIXES):
        return None
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("transcript")
    parser.add_argument("--grep", help="regex; print only matching messages (case-insensitive)")
    parser.add_argument("--width", type=int, default=400, help="characters per message")
    args = parser.parse_args()
    pattern = re.compile(args.grep, re.IGNORECASE) if args.grep else None

    with open(args.transcript, errors="replace") as f:
        for line_no, line in enumerate(f, 1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = human_text(record)
            if text is None or (pattern and not pattern.search(text)):
                continue
            stamp = record.get("timestamp", "")[5:16]
            print(f"L{line_no} {stamp} | {text[: args.width].replace(chr(10), ' ')}")


if __name__ == "__main__":
    main()
