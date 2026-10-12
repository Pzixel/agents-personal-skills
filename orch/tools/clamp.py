#!/usr/bin/env python3
"""Clamp long command output to its head and tail (pure) plus a tiny CLI.

Compilers put the decisive lines first, test runners last, so both ends survive.
"""

import argparse
import sys


def _size(lines):
    return len("".join(lines).encode("utf-8", "replace"))


def clamp(text, head_lines, tail_lines, max_bytes, path=""):
    if len(text.encode("utf-8", "replace")) <= max_bytes:
        return text
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    head = lines[:head_lines]
    tail = lines[max(len(head), len(lines) - tail_lines):]

    def render():
        omitted = lines[len(head):len(lines) - len(tail)]
        marker = "[... %d lines, %.1f KB omitted; full output: %s]\n" % (
            len(omitted), _size(omitted) / 1024, path)
        return "".join(head) + marker + "".join(tail)

    out = render()
    while len(out.encode("utf-8", "replace")) > max_bytes and (head or len(tail) > 1):
        if head:
            head.pop()
        else:
            tail.pop(0)
        out = render()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--head", type=int, required=True)
    ap.add_argument("--tail", type=int, required=True)
    ap.add_argument("--max-bytes", type=int, required=True)
    a = ap.parse_args(argv)
    with open(a.path, errors="replace") as fh:
        sys.stdout.write(clamp(fh.read(), a.head, a.tail, a.max_bytes, a.path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
