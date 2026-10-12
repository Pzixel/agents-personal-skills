#!/usr/bin/env python3
"""Static audit of Lua scripts for Dragonfly round-trip and coordination costs.

Pure stdlib. Exit code is always 0: findings are advice, not failures.
Each finding is `path:line: <rule-id>: <problem> -> <fix>`.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

RULES = {
    "call-in-loop": "redis.call inside for/while/repeat: one round trip per item",
    "batchable-hash": "per-item HGET/HSET/HDEL/ZADD that has a multi-argument form",
    "read-past-bound": "read issued before the loop's own bound check rejects the item",
    "cjson-hot": "cjson.encode of an accumulated table on the hot path",
    "undeclared-key": "key expression built from concatenation/ARGV instead of KEYS",
}

# Commands whose cost is a read the caller may not need yet.
READ_CMDS = {"GET", "HGET", "HMGET", "HGETALL", "MGET", "ZSCORE", "ZRANGE",
             "ZRANGEBYSCORE", "LRANGE", "SMEMBERS", "SISMEMBER", "EXISTS"}
BATCHABLE = {"HGET": "HMGET KEYS[n] field1 field2 ...",
             "HSET": "one HSET with every field/value pair",
             "HDEL": "one HDEL with every field",
             "ZADD": "one ZADD with every score/member pair"}
# Identifiers that make `#acc < x` a batch-size bound rather than an arity check.
BOUND_WORDS = ("batch", "limit", "size", "max", "window", "count", "quota")

_LONG_BRACKET = re.compile(r"(--)?\[(=*)\[")
_TOKEN = re.compile(r"\b(for|while|repeat|do|then|elseif|if|function|end|until)\b")
_CALL = re.compile(r"\bredis\.(call|pcall|acall)\s*\(")
_CJSON = re.compile(r"\bcjson\.encode\s*\(\s*([A-Za-z_][\w.]*)\s*\)")
_KEYS_ALIAS = re.compile(r"([A-Za-z_]\w*)\s*=\s*(KEYS\s*\[|[A-Za-z_]\w*_key\b)")
_ACCUM = re.compile(r"([A-Za-z_]\w*)\s*\[\s*#\s*\1\s*\+\s*1\s*\]\s*=|"
                    r"table\.insert\s*\(\s*([A-Za-z_]\w*)\s*,")
_TABLE_LITERAL = re.compile(r"\b([A-Za-z_]\w*)\s*=\s*\{")
_BOUND = re.compile(r"#\s*([A-Za-z_]\w*)\s*(<|>=)\s*([A-Za-z_]\w*|\d+)")


# ---------------------------------------------------------------------------
# Pure: lexing
# ---------------------------------------------------------------------------

def blank_noncode(src: str) -> str:
    """Replace comment and string bodies with spaces, preserving every offset."""
    out = list(src)
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in "\"'":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            for k in range(i + 1, min(j, n)):
                if out[k] != "\n":
                    out[k] = " "
            i = j + 1
            continue
        m = _LONG_BRACKET.match(src, i)
        if m:
            close = "]" + m.group(2) + "]"
            j = src.find(close, m.end())
            j = n if j < 0 else j + len(close)
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
            continue
        if src.startswith("--", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        i += 1
    return "".join(out)


def split_args(code: str, open_paren: int) -> tuple[list[str], int]:
    """Top-level comma split of the argument list starting at `open_paren`."""
    depth, start, args = 0, open_paren + 1, []
    i = open_paren
    while i < len(code):
        c = code[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                args.append(code[start:i])
                return [a.strip() for a in args], i
        elif c == "," and depth == 1:
            args.append(code[start:i])
            start = i + 1
        i += 1
    return [a.strip() for a in args], len(code)


def block_stacks(code: str) -> list[tuple[int, list[tuple[str, int]]]]:
    """Every block keyword position paired with the frame stack after it.

    Frames are (kind, start_line) with kind in {"loop", "block"}.
    """
    stack: list[tuple[str, int]] = []
    events: list[tuple[int, list[tuple[str, int]]]] = []
    pending_loop = skip_then = False
    for m in _TOKEN.finditer(code):
        tok = m.group(1)
        line = code.count("\n", 0, m.start()) + 1
        if tok in ("for", "while"):
            pending_loop = True
        elif tok == "repeat":
            stack.append(("loop", line))
        elif tok == "do":
            stack.append(("loop" if pending_loop else "block", line))
            pending_loop = False
        elif tok == "elseif":
            skip_then = True
        elif tok == "then":
            if skip_then:
                skip_then = False
            else:
                stack.append(("block", line))
        elif tok == "function":
            stack.append(("block", line))
        elif tok in ("end", "until") and stack:
            stack.pop()
        events.append((m.start(), list(stack)))
    return events


def stack_at(events, pos: int) -> list[tuple[str, int]]:
    lo, hi = 0, len(events)
    while lo < hi:
        mid = (lo + hi) // 2
        if events[mid][0] < pos:
            lo = mid + 1
        else:
            hi = mid
    return events[lo - 1][1] if lo else []


def innermost_loop(stack) -> int | None:
    for kind, line in reversed(stack):
        if kind == "loop":
            return line
    return None


# ---------------------------------------------------------------------------
# Pure: rules
# ---------------------------------------------------------------------------

def command_of(args: list[str]) -> str:
    """Literal command name upper-cased; a non-literal stays as written, because
    a dynamically dispatched command is itself worth seeing in the report."""
    if not args:
        return ""
    a = args[0]
    if len(a) > 1 and a[0] in "\"'" and a[-1] == a[0]:
        return a[1:-1].upper()
    return a


def is_declared_key(expr: str, aliases: set[str]) -> bool:
    expr = expr.strip()
    if expr.startswith("KEYS["):
        return True
    return expr.isidentifier() and expr in aliases


def keys_aliases(code: str) -> set[str]:
    """Locals that carry a KEYS[n] value (`local versions_key = KEYS[5]`)."""
    return {m.group(1) for m in _KEYS_ALIAS.finditer(code)}


def accumulated_tables(code: str) -> set[str]:
    names = set()
    for m in _ACCUM.finditer(code):
        names.add(m.group(1) or m.group(2))
    return names


def bounded_loops(code: str, events) -> dict[int, int]:
    """loop-start-line -> position of the first `#acc < batch_size`-style guard."""
    out: dict[int, int] = {}
    for m in _BOUND.finditer(code):
        rhs = m.group(3)
        if not (rhs.isdigit() or any(w in rhs.lower() for w in BOUND_WORDS)):
            continue
        loop = innermost_loop(stack_at(events, m.start()))
        if loop is not None and loop not in out:
            out[loop] = m.start()
    return out


def audit_source(path: str, src: str) -> list[tuple[int, str, str, str]]:
    """Pure: (line, rule-id, problem, fix) for one script body."""
    code = blank_noncode(src)
    events = block_stacks(code)
    aliases = keys_aliases(code)
    accum = accumulated_tables(code) | {m.group(1) for m in _TABLE_LITERAL.finditer(code)}
    bounds = bounded_loops(code, events)
    findings: list[tuple[int, str, str, str]] = []
    seen: set[tuple[str, int, str]] = set()
    seen_bound: set[int] = set()

    for m in _CALL.finditer(code):
        args, end = split_args(src, m.end() - 1)
        cmd = command_of(args)
        line = code.count("\n", 0, m.start()) + 1
        loop = innermost_loop(stack_at(events, m.start()))
        raw = src[m.start():end + 1].replace("\n", " ")

        if len(args) > 1 and not is_declared_key(args[1], aliases) \
                and (".." in args[1] or "ARGV[" in args[1]):
            findings.append((line, "undeclared-key",
                             f"{cmd} key comes from {args[1].strip()[:40]}, not KEYS",
                             "pass the key in KEYS so the shard set is known before "
                             "execution; Dragonfly schedules on declared keys only"))

        if loop is None:
            continue
        key = (path, loop, cmd)
        if cmd in BATCHABLE:
            if key not in seen:
                seen.add(key)
                findings.append((line, "batchable-hash",
                                 f"per-item {cmd} in the loop opened at line {loop}",
                                 f"hoist it out of the loop: {BATCHABLE[cmd]}"))
        elif key not in seen:
            seen.add(key)
            findings.append((line, "call-in-loop",
                             f"{cmd or raw[:30]} runs once per iteration "
                             f"(loop at line {loop})",
                             "collect the arguments in the loop and issue one "
                             "variadic call, or fetch everything up front"))

        guard = bounds.get(loop)
        if guard is not None and m.start() < guard and cmd in READ_CMDS \
                and loop not in seen_bound:
            seen_bound.add(loop)
            findings.append((line, "read-past-bound",
                             f"{cmd} is issued for every candidate, but the loop's "
                             f"own bound check at line "
                             f"{code.count(chr(10), 0, guard) + 1} discards most of them",
                             "move the read after the bound check so only the "
                             "accepted items cost a round trip"))

    for m in _CJSON.finditer(code):
        name = m.group(1)
        if name not in accum:
            continue
        line = code.count("\n", 0, m.start()) + 1
        findings.append((line, "cjson-hot",
                         f"cjson.encode({name}) serialises a table the script "
                         f"builds up; its size grows with the input",
                         "cap the encoded size, or emit the pieces as separate "
                         "values instead of one grown JSON blob"))

    return sorted(findings)


# ---------------------------------------------------------------------------
# Workflow: files in, lines out
# ---------------------------------------------------------------------------

def lua_files(target: Path) -> list[Path]:
    if target.is_dir():
        return sorted(target.rglob("*.lua"))
    return [target]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="exit code is 0 even when findings are printed")
    p.add_argument("target", nargs="?", type=Path,
                   help="a .lua file or a directory searched recursively")
    p.add_argument("--rules", action="store_true", help="list rule ids and exit")
    p.add_argument("--rule", action="append", default=[], metavar="ID",
                   help="only report these rule ids (repeatable)")
    p.add_argument("--exclude", action="append", default=[], metavar="ID",
                   help="suppress these rule ids (repeatable)")
    p.add_argument("--count", action="store_true",
                   help="print per-file and per-rule totals instead of findings")
    a = p.parse_args(argv)

    if a.rules:
        for rid, desc in RULES.items():
            print(f"{rid}: {desc}")
        return 0
    if a.target is None:
        p.error("target is required unless --rules is given")

    per_file: dict[str, int] = {}
    per_rule: dict[str, int] = {}
    for f in lua_files(a.target):
        for line, rid, problem, fix in audit_source(f.name, f.read_text()):
            if a.rule and rid not in a.rule:
                continue
            if rid in a.exclude:
                continue
            per_file[f.name] = per_file.get(f.name, 0) + 1
            per_rule[rid] = per_rule.get(rid, 0) + 1
            if not a.count:
                print(f"{f}:{line}: {rid}: {problem} -> {fix}")
    if a.count:
        for name, n in sorted(per_file.items(), key=lambda kv: -kv[1]):
            print(f"{n:4d}  {name}")
        for rid, n in sorted(per_rule.items(), key=lambda kv: -kv[1]):
            print(f"{n:4d}  rule:{rid}")
    print(f"-- {sum(per_file.values())} findings in {len(per_file)} files",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
