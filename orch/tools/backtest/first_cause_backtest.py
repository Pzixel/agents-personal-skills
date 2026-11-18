"""Backtest of the first-cause `Unread:` return (reply-lint) on stored main-session transcripts.

usage: python3 first_cause_backtest.py history [days] [--old-rev REV]
       python3 first_cause_backtest.py after [days] [--limit N]

history: every main-session stop reply of the last ``days`` (default 28) whose first line opens with `Proven:`,
de-duplicated by text (earliest copy kept). Prints how many the old revision's rule and the current rule return
(pure decision and with the per-turn budget), how many arrived with 0, 1 or 2+ first-cause returns already spent in
their turn, how many drew another reply-lint rule on the same stop (read from the Stop hook feedback that followed
it in the transcript), and 5 raw first lines.
after: the first ``limit`` (default 20) stored returns of the `Unread:` reason, each with the next stop reply's
first line, the class of its `Unread:` content (none / listed / absent) and the tool calls in between.
Reads `~/.claude/projects/*/*.jsonl` and `~/.claude-accounts/*/projects/*/*.jsonl` (subagent files and harness
directories under `/private` excluded).
"""
import datetime
import glob
import importlib.util
import os
import re
import subprocess
import sys
import tempfile

TOOLS = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, TOOLS)
import gate_policy  # noqa: E402
import transcript  # noqa: E402

UNREAD_RETURN = "carries a line `Unread:`"
_UNREAD_LINE_RE = re.compile(r"^[ \t>*_\-+]*`?Unread\b[^\n]*", re.IGNORECASE | re.MULTILINE)


def old_module(rev):
    source = subprocess.run(["git", "-C", TOOLS, "show", rev + ":orch/tools/gate_policy.py"], check=True,
                            stdout=subprocess.PIPE, universal_newlines=True).stdout
    # Loaded from the tools directory: the module resolves its sibling paths from its own file.
    fd, path = tempfile.mkstemp(suffix=".py", prefix=".gate_policy_old_", dir=TOOLS)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(source)
        spec = importlib.util.spec_from_file_location("gate_policy_old", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        os.remove(path)
    return module


def transcripts(days):
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    paths = glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl"))
    paths += glob.glob(os.path.expanduser("~/.claude-accounts/*/projects/*/*.jsonl"))
    # Replay and bench harnesses run in temporary directories (`/private/tmp`, `/private/var/folders`); real
    # sessions run in a checkout. Their replies are copies of a witness with new wording, so text de-duplication
    # does not remove them.
    paths = {os.path.realpath(p) for p in paths}  # one account directory may be linked under another path
    return cutoff, sorted(p for p in paths if os.path.getmtime(p) >= cutoff.timestamp()
                          and not os.path.basename(os.path.dirname(p)).startswith("-private-"))


def texts(entry):
    return [str(b.get("text") or "") for b in transcript.blocks_of(entry) if b.get("type") == "text"]


def feedback_of(entry):
    return [t for t in texts(entry) if t.startswith(transcript.STOP_FEEDBACK_PREFIX)]


def is_tool_result(entry):
    return any(b.get("type") == "tool_result" for b in transcript.blocks_of(entry))


def tool_uses(entry):
    return sum(b.get("type") == "tool_use" for b in transcript.blocks_of(entry))


def stop_replies(entries):
    """(index, reply text, this turn's Stop hook feedback before it) for every main-session assistant text that
    ended a stop: no tool call of the agent follows it before the next user entry, which is no tool result."""
    feedback = []
    for i, (_raw, entry) in enumerate(entries):
        if entry.get("type") == "system" and entry.get("subtype") == "turn_duration":
            feedback = []
        if entry.get("isSidechain"):
            continue
        if entry.get("type") == "user":
            feedback += feedback_of(entry)
        if entry.get("type") != "assistant" or tool_uses(entry):
            continue
        text = "\n".join(texts(entry)).strip()
        if not text:
            continue
        stop = True
        for _r, nxt in entries[i + 1:]:
            if nxt.get("isSidechain"):
                continue
            if nxt.get("type") == "assistant" and tool_uses(nxt):
                stop = False
                break
            if nxt.get("type") == "assistant" and texts(nxt):
                break
            if nxt.get("type") == "user":
                stop = not is_tool_result(nxt)
                break
        if stop:
            yield i, text, list(feedback)


def first_line(text):
    return next((ln for ln in text.splitlines() if ln.strip()), "")


def following_feedback(entries, i):
    out = []
    for _r, nxt in entries[i + 1:]:
        if nxt.get("type") == "assistant" and not nxt.get("isSidechain"):
            break
        if nxt.get("type") == "user":
            out += feedback_of(nxt)
    return " ".join(out)


def history(days, old_rev):
    old = old_module(old_rev)
    cutoff, paths = transcripts(days)
    seen, raw = {}, 0
    for path in paths:
        with open(path, errors="replace") as fh:
            if not re.search(r"Proven|Доказано", fh.read()):
                continue
        entries = transcript.entries_of(path)
        for i, text, feedback in stop_replies(entries):
            if not gate_policy._PROVEN_LINE_RE.match(first_line(text)):
                continue
            stamp = str(entries[i][1].get("timestamp") or "")
            if not stamp or transcript.epoch(stamp) < cutoff.timestamp():
                continue
            raw += 1
            if text in seen and seen[text]["stamp"] <= stamp:
                continue
            after = following_feedback(entries, i)
            seen[text] = {
                "stamp": stamp, "path": path, "line": i + 1, "first": first_line(text),
                "spent": sum(gate_policy.FIRST_CAUSE_MARKER in f for f in feedback), "later": bool(feedback),
                "old": old.first_paragraph_cause(text), "new": gate_policy.first_paragraph_cause(text),
                "other_rule": after.count(gate_policy.REPLY_LINT_MARKER)
                > after.count(gate_policy.FIRST_CAUSE_MARKER),
            }
    rows = sorted(seen.values(), key=lambda r: r["stamp"])
    slot = [r for r in rows if not r["later"] or r["spent"] < 2]
    print("window: last %d days from %s; transcripts read: %d" % (days, cutoff.isoformat()[:16], len(paths)))
    print("Proven: stop replies: raw %d, distinct %d" % (raw, len(rows)))
    print("old rule (%s) returns, pure decision: %d (%s)" % (
        old_rev, sum(bool(r["old"]) for r in rows),
        ", ".join("%s %d" % (k or "pass", sum(r["old"] == k for r in rows)) for k in sorted({r["old"] for r in rows}))))
    print("new rule returns, pure decision: %d (%s)" % (
        sum(bool(r["new"]) for r in rows),
        ", ".join("%s %d" % (k or "pass", sum(r["new"] == k for r in rows)) for k in sorted({r["new"] for r in rows}))))
    print("new rule returns with the per-turn budget (first stop, or later stop with < 2 spent): %d" % sum(
        bool(r["new"]) for r in slot))
    print("first-cause returns already spent in the turn: 0: %d, 1: %d, 2+: %d; arrived on a later stop: %d" % (
        sum(r["spent"] == 0 for r in rows), sum(r["spent"] == 1 for r in rows), sum(r["spent"] >= 2 for r in rows),
        sum(r["later"] for r in rows)))
    print("another reply-lint rule returned on the same stop: %d of %d" % (sum(r["other_rule"] for r in rows),
                                                                       len(rows)))
    print("per day (distinct, by first-copy date): %s" % ", ".join(
        "%s %d" % (d, sum(r["stamp"][:10] == d for r in rows)) for d in sorted({r["stamp"][:10] for r in rows})))
    projects = sorted({os.path.basename(os.path.dirname(r["path"])) for r in rows})
    print("per project directory (distinct): %s" % ", ".join(
        "%s %d" % (p, sum(os.path.basename(os.path.dirname(r["path"])) == p for r in rows)) for p in projects))
    print("5 raw first lines (most recent):")
    for r in rows[-5:]:
        print("  %s %s:%d  %s" % (r["stamp"][:16], os.path.basename(r["path"])[:8], r["line"], r["first"][:200]))


def unread_class(text):
    line = _UNREAD_LINE_RE.search(text)
    if not line:
        return "absent"
    return "none" if gate_policy._UNREAD_NONE_RE.search(text) else "listed"


def after(days, limit):
    _cutoff, paths = transcripts(days)
    found = []
    for path in paths:
        with open(path, errors="replace") as fh:
            if UNREAD_RETURN not in fh.read():
                continue
        entries = transcript.entries_of(path)
        for i, (_raw, entry) in enumerate(entries):
            if entry.get("type") != "user" or not any(UNREAD_RETURN in f for f in feedback_of(entry)):
                continue
            calls, reply = 0, None
            for j, (_r, nxt) in enumerate(entries[i + 1:], i + 1):
                if nxt.get("isSidechain") or nxt.get("type") != "assistant":
                    continue
                calls += tool_uses(nxt)
                if not tool_uses(nxt) and texts(nxt):
                    reply = "\n".join(texts(nxt)).strip()
                    break
            found.append((str(entry.get("timestamp") or ""), path, i + 1, calls, reply))
    found = sorted(set(found))
    print("Unread: returns stored: %d; first %d:" % (len(found), min(limit, len(found))))
    for stamp, path, line, calls, reply in found[:limit]:
        print("  %s %s:%d tool calls %d, Unread %s | %s" % (
            stamp[:16], os.path.basename(path)[:8], line, calls,
            unread_class(reply) if reply else "no reply", first_line(reply or "")[:160]))


def main(argv):
    mode = argv[1] if len(argv) > 1 else "history"
    opts, rest, args = {}, [], iter(argv[2:])
    for arg in args:
        if arg.startswith("--"):
            opts[arg] = next(args)
        else:
            rest.append(arg)
    days = int(rest[0]) if rest else 28
    if mode == "history":
        history(days, opts.get("--old-rev", "3e3bc78"))
    elif mode == "after":
        after(days, int(opts.get("--limit", 20)))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
