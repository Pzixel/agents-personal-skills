"""Which merged worktrees and branches to remove, and when a session leaves uncommitted work.

Cleanup contract: work is finished when it reached the default branch, whatever
the branch was called. A branch is finished when its tip equals the head of any
merged pull request from it, or of a pull request from any branch when the
tip is off the default branch's first-parent line, when every commit it has beyond origin/<default> has a
content already there (merging it into origin/<default> leaves the tree unchanged;
at least one own commit), or when
its tip reached the default branch through a merge. A branch created at a
first-parent default commit with no own commits is fresh and kept. A detached
worktree is finished when its HEAD is an ancestor of origin/<default>; only the
worktree goes, there is no branch. Finished worktrees that are dirty, busy or
the main checkout are kept, and so is their branch. The default branch is
never deleted. Remote branches are left to GitHub's delete-branch-on-merge.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass


WT_PATH = re.compile(r"(/[^\s\"'`;|&<>()]+?)/\.worktrees/([A-Za-z0-9._-]+)")
WT_REL = re.compile(r"(?<![\w/])(?:\./)?\.worktrees/([A-Za-z0-9._-]+)")
_FILE_WRITERS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})


def written_worktrees(tool_name, tool_input, cwd=None):
    """Worktree paths a tool call may have written into.

    File-writing tools count by file_path (NotebookEdit also notebook_path),
    Bash by its command text; a relative .worktrees/<name> resolves against cwd,
    the call's working directory, and counts for nothing without it. Delegation
    and read-only tools name worktrees without writing there, so they count for
    nothing.
    """
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    if tool_name in _FILE_WRITERS:
        text = " ".join(str(tool_input.get(k) or "") for k in ("file_path", "notebook_path"))
    elif tool_name == "Bash":
        text = str(tool_input.get("command") or "")
    else:
        return set()
    found = {m.group(0) for m in WT_PATH.finditer(text)}
    if cwd:
        found.update(os.path.normpath(os.path.join(cwd, m.group(0))) for m in WT_REL.finditer(text))
    return found


DETACHED_FRESH_S = 2 * 86400


@dataclass(frozen=True)
class Worktree:
    path: str
    branch: str | None
    head: str
    is_main: bool
    dirty: bool
    busy: bool


@dataclass(frozen=True)
class Cleanup:
    remove_worktrees: tuple[str, ...]
    delete_local_branches: tuple[str, ...]
    kept: tuple[tuple[str, str], ...]


def decide_cleanup(worktrees, local_branches, merged_heads, default_branch,
                   merged_into_default, default_first_parent, content_merged=(),
                   detached_in_default=None):
    """local_branches: name -> tip; merged_heads: PR head branch name -> merged head oids;
    merged_into_default: names whose tip is an ancestor of origin/<default>;
    default_first_parent: oids on the first-parent history of origin/<default>;
    content_merged: names with at least one own commit whose merge into origin/<default>
    leaves its tree unchanged; detached_in_default: path -> directory age in seconds
    for detached worktrees whose HEAD is an ancestor of origin/<default>. A detached
    worktree at a first-parent default commit is fresh like a new branch and kept
    until it is DETACHED_FRESH_S old.
    """
    any_head = set().union(*merged_heads.values()) if merged_heads else set()
    finished = {
        name
        for name, tip in local_branches.items()
        if name != default_branch
        and (
            tip in merged_heads.get(name, ())
            # another branch's merged head: a fresh branch cut at a first-parent
            # default commit that a fast-forward merge left there is not finished
            or (tip in any_head and tip not in default_first_parent)
            or name in content_merged
            or (name in merged_into_default and tip not in default_first_parent)
        )
    }
    remove, kept, blocked = [], [], set()
    for wt in worktrees:
        if wt.branch is None:
            age = (detached_in_default or {}).get(wt.path)
            if age is None or (wt.head in default_first_parent and age < DETACHED_FRESH_S):
                continue
        elif wt.branch not in finished:
            continue
        reason = ("main checkout" if wt.is_main else "uncommitted changes" if wt.dirty
                  else "a process works inside" if wt.busy else None)
        if reason:
            if wt.branch:
                blocked.add(wt.branch)
            kept.append((wt.path, reason))
        else:
            remove.append(wt.path)
    local = sorted(finished - blocked)
    return Cleanup(tuple(remove), tuple(local), tuple(kept))


def uncommitted_work_decision(session_start, dirty, seen, handed_off=()):
    """Stop gate over this session's uncommitted worktree edits.

    session_start: epoch seconds of the session's first transcript entry;
    dirty: worktree path -> [(file, mtime)] of its uncommitted, non-ignored files;
    seen: fingerprints this session was already stopped for;
    handed_off: worktrees a handoff file outside them names. A handoff*.md edited
    after session_start inside the worktree also settles it.
    A file edited after session_start belongs to this session; files that all
    predate it are someone else's. Returns (worktrees to report, fingerprints
    to remember); an empty first item lets the stop through.
    """
    report, prints = [], []
    for path in sorted(dirty):
        fresh = sorted((f, m) for f, m in dirty[path] if m > session_start)
        handoff = any(os.path.basename(f).lower().startswith("handoff") and f.lower().endswith(".md")
                      for f, _ in fresh)
        if not fresh or handoff or path in handed_off:
            continue
        fp = hashlib.sha256(repr((path, fresh)).encode()).hexdigest()[:16]
        if fp not in seen:
            report.append(path)
            prints.append(fp)
    return report, prints
