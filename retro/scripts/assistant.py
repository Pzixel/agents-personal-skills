#!/usr/bin/env python3
"""Print assistant text from a Claude Code transcript for a line range.

Usage:

    assistant.py <transcript.jsonl> FROM TO [--width N]

FROM and TO are 1-based JSONL line numbers, inclusive, as printed by
humans.py. Tool calls and short fragments are skipped.
"""

from __future__ import annotations

import argparse
import json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("transcript")
    parser.add_argument("start", type=int)
    parser.add_argument("end", type=int)
    parser.add_argument("--width", type=int, default=1500, help="characters per text block")
    args = parser.parse_args()

    with open(args.transcript, errors="replace") as f:
        for line_no, line in enumerate(f, 1):
            if line_no < args.start:
                continue
            if line_no > args.end:
                break
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("type") != "assistant":
                continue
            stamp = record.get("timestamp", "")[5:16]
            for block in record.get("message", {}).get("content", []):
                if isinstance(block, dict) and block.get("type") == "text" and len(block["text"]) > 40:
                    print(f"A L{line_no} {stamp} | {block['text'][: args.width]}\n")


if __name__ == "__main__":
    main()
