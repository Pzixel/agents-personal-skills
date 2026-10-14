#!/usr/bin/env python3
"""Stop hook: refuse once to end a turn that leaves this session's edits uncommitted in a worktree.

Candidates are the linked worktrees under <main-checkout>/.worktrees/ of the
repository containing cwd and of every repository whose .worktrees/<name> path
appears in this session's transcript. A dirty file counts as this session's
when its mtime is after the session's first transcript timestamp: no
per-session ownership record exists, so mtime is the proxy. The decision is
tools/worktree_policy.uncommitted_work_decision; fingerprints already reported
live in ~/.claude/tmp/worktree-dirty-gate/<session>.json. Fails open.
"""

import concurrent.futures
import datetime
import json
import os
import re
import subprocess
import sys

_PLUGIN_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
sys.path.insert(0, os.path.realpath(
    _PLUGIN_TOOLS if os.path.isdir(_PLUGIN_TOOLS) else os.path.expanduser("~/.claude/tools")))

from worktree_policy import uncommitted_work_decision  # noqa: E402

STATE_DIR = os.path.expanduser("~/.claude/tmp/worktree-dirty-gate")
WT_PATH = re.compile(r"(/[^\s\"'`;|&<>()]+?)/\.worktrees/([A-Za-z0-9._-]+)")
MAX_WORKTREES = 24


def git(args, cwd):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10)
    return p.stdout if p.returncode == 0 else ""


def main_checkout(path):
    out = git(["worktree", "list", "--porcelain"], path)
    return out.split("\n", 1)[0].removeprefix("worktree ").strip() if out else ""


def session_start_and_repos(transcript):
    start, roots = None, set()
    with open(transcript, errors="replace") as fh:
        for line in fh:
            if start is None:
                m = re.search(r'"timestamp":"([^"]+)"', line)
                if m:
                    start = datetime.datetime.fromisoformat(m.group(1).replace("Z", "+00:00")).timestamp()
            if '"tool_use"' in line:
                roots.update(m.group(1) for m in WT_PATH.finditer(line))
    return start, roots


def linked_worktrees(main):
    prefix = os.path.join(main, ".worktrees") + os.sep
    out = git(["worktree", "list", "--porcelain"], main)
    return [l[9:] for l in out.splitlines() if l.startswith("worktree ") and l[9:].startswith(prefix)]


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
        try:
            files.append((e[3:], os.lstat(os.path.join(wt, e[3:])).st_mtime))
        except OSError:
            pass  # deleted: no mtime to date it
    return files


def main():
    payload = json.loads(sys.stdin.read() or "{}")
    if payload.get("agent_type") or payload.get("agent_id") or payload.get("stop_hook_active"):
        return
    transcript, session = payload.get("transcript_path") or "", payload.get("session_id") or ""
    if not session or not os.path.isfile(transcript):
        return
    start, roots = session_start_and_repos(transcript)
    if start is None:
        return
    mains = {main_checkout(payload.get("cwd") or os.getcwd())}
    mains.update(main_checkout(r) for r in roots if os.path.isdir(r))
    wts = sorted({w for m in mains if m for w in linked_worktrees(m)})[:MAX_WORKTREES]
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        dirty = dict(zip(wts, pool.map(dirty_files, wts)))
    state = os.path.join(STATE_DIR, session + ".json")
    seen = set(json.load(open(state))) if os.path.isfile(state) else set()
    report, prints = uncommitted_work_decision(start, dirty, seen)
    if not report:
        return
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(state, "w") as fh:
        json.dump(sorted(seen | set(prints)), fh)
    reason = ("This session edited files that are still uncommitted in: " + ", ".join(report)
              + ". Commit them there (a WIP commit is fine) or write a handoff .md naming the "
              "worktree, what is done and the next step; then end the turn.")
    json.dump({"decision": "block", "reason": reason}, sys.stdout)


try:
    main()
except BaseException:
    pass
sys.exit(0)
