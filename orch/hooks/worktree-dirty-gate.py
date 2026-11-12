#!/usr/bin/env python3
"""Stop hook: refuse once to end a turn that leaves this session's edits uncommitted in a worktree.

Candidates are the .worktrees/<name> paths this session's writing tools named
(Edit, Write, MultiEdit, NotebookEdit by file path; Bash by command text, a
relative path resolved against the entry's cwd), plus cwd when it is inside
one. Delegation (Agent, Task, SendMessage) and read-only tools never make a
worktree this session's: a builder's edits there are its own. When a report is
due, one more pass (worktree_policy.delegate_held) holds back a worktree that a
live writing delegate's prompt names and the session itself never wrote inside
(file tool, or a Bash write target from gate_policy.bash_effects); it is
reported once that delegate has finished. Each such decision is logged to
~/.claude/tmp/worktree-dirty-gate-log.jsonl. A dirty file
counts as this session's when its mtime is after the session's first
transcript timestamp: no per-session ownership record exists, so mtime is the
proxy. The decision is
tools/worktree_policy.uncommitted_work_decision; fingerprints already reported
live in ~/.claude/tmp/worktree-dirty-gate.json, entries kept 7 days. Fails open.
"""

import concurrent.futures
import datetime
import json
import os
import re
import subprocess
import time
import sys

_PLUGIN_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
sys.path.insert(0, os.path.realpath(
    _PLUGIN_TOOLS if os.path.isdir(_PLUGIN_TOOLS) else os.path.expanduser("~/.claude/tools")))

from gate_policy import bash_effects  # noqa: E402
from transcript import _AGENT_LAUNCHED_RE, background_watch_scan, blocks_of, entries_of, result_text  # noqa: E402
from worktree_policy import (  # noqa: E402
    WT_PATH, delegate_held, own_writes, uncommitted_work_decision, written_worktrees)

STATE = os.path.expanduser("~/.claude/tmp/worktree-dirty-gate.json")
LOG = os.path.expanduser("~/.claude/tmp/worktree-dirty-gate-log.jsonl")
HANDOFF = re.compile(r"(/[^\s\"'`;|&<>()]*/[Hh][Aa][Nn][Dd][Oo][Ff][Ff][^/\s\"'`]*\.md)")
MAX_WORKTREES = 24
STATE_TTL_S = 7 * 86400


def git(args, cwd):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10)
    return p.stdout if p.returncode == 0 else ""


def session_facts(transcript):
    """First timestamp, worktrees written by tool calls, handoff*.md paths named in any tool input."""
    start, wts, handoffs = None, set(), set()
    with open(transcript, errors="replace") as fh:
        for line in fh:
            if start is None:
                m = re.search(r'"timestamp":"([^"]+)"', line)
                if m:
                    start = datetime.datetime.fromisoformat(m.group(1).replace("Z", "+00:00")).timestamp()
            if '"tool_use"' not in line:
                continue
            entry = json.loads(line)
            content = (entry.get("message") or {}).get("content")
            for item in content if isinstance(content, list) else ():
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    text = json.dumps(item.get("input"))
                    wts.update(written_worktrees(item.get("name"), item.get("input"), entry.get("cwd")))
                    handoffs.update(HANDOFF.findall(text))
    return start, wts, handoffs


def delegate_facts(entries):
    """Worktrees the session itself wrote inside, and (type, worktrees named, live) per async agent.

    An agent names the worktrees its spawn prompt and every SendMessage to its id
    name; it is live while background_watch_scan has it open at the end.
    """
    agents = {}
    for snap in background_watch_scan(entries, "worktree-dirty-gate"):
        agents = snap["agents"]
    own, spawns, named, kinds = set(), {}, {}, {}
    for _, entry in entries:
        if entry.get("isSidechain"):
            continue
        for block in blocks_of(entry):
            if block.get("type") == "tool_use":
                name, args = block.get("name"), block.get("input") or {}
                text = json.dumps(args, ensure_ascii=False)
                if name == "Agent":
                    spawns[block.get("id")] = (args.get("subagent_type"), {m.group(0) for m in WT_PATH.finditer(text)})
                elif name == "SendMessage":
                    named.setdefault(str(args.get("to") or ""), set()).update(m.group(0) for m in WT_PATH.finditer(text))
                elif name != "Bash":
                    own |= own_writes(name, args, entry.get("cwd"), ())
                elif ".worktrees/" in text or "/.worktrees/" in str(entry.get("cwd")):
                    # a Bash write lands in a worktree only through a path or a cwd that names one
                    writes = bash_effects(str(args.get("command") or ""), entry.get("cwd") or "")["writes"]
                    own |= own_writes(name, args, entry.get("cwd"), writes)
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in spawns:
                launched = _AGENT_LAUNCHED_RE.match(result_text(block).strip())
                if launched:
                    kind, wts = spawns[block.get("tool_use_id")]
                    kinds[launched.group(1)] = kind
                    named.setdefault(launched.group(1), set()).update(wts)
    delegates = [(kinds.get(a), named.get(a, set()), not v["closed"]) for a, v in agents.items()]
    return own, delegates


def write_log(record):
    try:
        record["ts"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass


def own_worktree(cwd):
    m = WT_PATH.match(cwd or "")
    return m.group(0) if m else None


def handed_off(handoffs, wts, start):
    """Worktrees named inside a handoff file written after session start."""
    named = set()
    for h in handoffs:
        try:
            if os.path.getmtime(h) <= start:
                continue
            text = open(h, errors="replace").read(200_000)
        except OSError:
            continue
        named.update(w for w in wts if w in text)
    return named


def load_state():
    try:
        return json.load(open(STATE))
    except (OSError, ValueError):
        return {}


def dirty_files(wt):
    out = git(["status", "--porcelain", "-z"], wt)
    files, entries = [], out.split("\0")
    i = 0
    while i < len(entries):
        e = entries[i]
        i += 1
        if len(e) < 4:
            continue
        if e[0] in "RC":
            i += 1  # rename source follows
        path = os.path.join(wt, e[3:])
        try:
            files.append((e[3:], os.lstat(path).st_mtime))
        except OSError:
            # deleted: the nearest surviving directory's mtime dates the removal
            parent = os.path.dirname(path)
            while not os.path.isdir(parent) and len(parent) > len(wt):
                parent = os.path.dirname(parent)
            files.append((e[3:], os.lstat(parent).st_mtime))
    return files


def main():
    payload = json.loads(sys.stdin.read() or "{}")
    if payload.get("agent_type") or payload.get("agent_id") or payload.get("stop_hook_active"):
        return
    transcript, session = payload.get("transcript_path") or "", payload.get("session_id") or ""
    if not session or not os.path.isfile(transcript):
        return
    start, named, handoffs = session_facts(transcript)
    if start is None:
        return
    named.add(own_worktree(payload.get("cwd") or os.getcwd()))
    wts = sorted(w for w in named if w and os.path.isfile(os.path.join(w, ".git")))[:MAX_WORKTREES]
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        dirty = dict(zip(wts, pool.map(dirty_files, wts)))
    state = load_state()
    seen = set(state.get(session, {}).get("prints", ()))
    report, prints = uncommitted_work_decision(start, dirty, seen, handed_off(handoffs, wts, start))
    if not report:
        return
    own, delegates = delegate_facts(entries_of(transcript))
    dirty_report = report
    report, prints = delegate_held(report, prints, own, delegates)
    write_log({"session": session, "decision": "block" if report else "allow", "dirty": dirty_report,
               "reported": report, "own": sorted(own & set(dirty_report)),
               "live_agents": [[k, sorted(n)] for k, n, live in delegates if live]})
    if not report:
        return
    now = time.time()
    state = {k: v for k, v in state.items() if now - v["at"] < STATE_TTL_S}
    state[session] = {"at": now, "prints": sorted(seen | set(prints))}
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as fh:
        json.dump(state, fh)
    reason = ("This session edited files that are still uncommitted in: " + ", ".join(report)
              + ". Commit them there (a WIP commit is fine), or write a HANDOFF.md (inside the "
              "worktree, or elsewhere naming it) with what is done and the next step; then end the turn.")
    json.dump({"decision": "block", "reason": reason}, sys.stdout)


try:
    main()
except BaseException:
    pass
sys.exit(0)
