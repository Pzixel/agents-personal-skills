"""Which merged worktrees and branches to remove, and when a session leaves uncommitted work.

Cleanup contract: work is finished when it reached the default branch, whatever
the branch was called. A branch is finished when its tip equals the head of any
merged pull request from it, or of a pull request from any branch when the
tip is off the default branch's first-parent line, when every commit it has beyond origin/<default> has a
patch-equivalent commit there (and it has at least one such commit), or when
its tip reached the default branch through a merge. A branch created at a
first-parent default commit with no own commits is fresh and kept. A detached
worktree is finished when its HEAD is an ancestor of origin/<default>; only the
worktree goes, there is no branch. Finished worktrees that are dirty, busy or
the main checkout are kept, and so is their branch. The default branch is
never deleted. Remote branches are left to GitHub's delete-branch-on-merge.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


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
                   merged_into_default, default_first_parent, patch_merged=(),
                   detached_in_default=()):
    """local_branches: name -> tip; merged_heads: PR head branch name -> merged head oids;
    merged_into_default: names whose tip is an ancestor of origin/<default>;
    default_first_parent: oids on the first-parent history of origin/<default>;
    patch_merged: names whose own commits all have patch equivalents in origin/<default>
    (at least one own commit); detached_in_default: paths of detached worktrees whose
    HEAD is an ancestor of origin/<default>.
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
            or name in patch_merged
            or (name in merged_into_default and tip not in default_first_parent)
        )
    }
    remove, kept, blocked = [], [], set()
    for wt in worktrees:
        if wt.branch is None:
            if wt.path not in detached_in_default:
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


def uncommitted_work_decision(session_start, dirty, seen):
    """Stop gate over this session's uncommitted worktree edits.

    session_start: epoch seconds of the session's first transcript entry;
    dirty: worktree path -> [(file, mtime)] of its uncommitted, non-ignored files;
    seen: fingerprints this session was already stopped for.
    A file edited after session_start belongs to this session; files that all
    predate it are someone else's. Returns (worktrees to report, fingerprints
    to remember); an empty first item lets the stop through.
    """
    report, prints = [], []
    for path in sorted(dirty):
        fresh = sorted((f, m) for f, m in dirty[path] if m > session_start)
        if not fresh:
            continue
        fp = hashlib.sha256(repr((path, fresh)).encode()).hexdigest()[:16]
        if fp not in seen:
            report.append(path)
            prints.append(fp)
    return report, prints
