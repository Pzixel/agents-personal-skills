"""Which merged branches and worktrees to remove.

Contract: a branch is finished when a pull request from it was merged with
exactly the branch's current tip. Such a branch is deleted locally and its
linked worktree is removed, unless the worktree has uncommitted changes or a running process works inside it. A branch with no
merged pull request, or with commits after the merged head, is kept. The main
checkout and the default branch are never removed. Remote branches are
left to GitHub's delete-branch-on-merge setting.
"""

from __future__ import annotations

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
                   merged_into_default, default_first_parent):
    """local_branches: name -> tip; merged_heads: name -> set of merged PR head oids;
    merged_into_default: names whose tip is an ancestor of origin/<default>;
    default_first_parent: oids on the first-parent history of origin/<default>.

    A branch is also finished when its tip reached the default branch through a
    merge (ancestor, but off the first-parent line); a branch created at a
    first-parent commit is fresh and kept. The default branch is never deleted.
    """
    finished = {
        name
        for name, tip in local_branches.items()
        if name != default_branch
        and (
            tip in merged_heads.get(name, ())
            or (name in merged_into_default and tip not in default_first_parent)
        )
    }
    remove, kept, blocked = [], [], set()
    for wt in worktrees:
        if wt.branch not in finished:
            continue
        if wt.is_main:
            blocked.add(wt.branch)
            kept.append((wt.path, "main checkout"))
        elif wt.dirty:
            blocked.add(wt.branch)
            kept.append((wt.path, "uncommitted changes"))
        elif wt.busy:
            blocked.add(wt.branch)
            kept.append((wt.path, "a process works inside"))
        else:
            remove.append(wt.path)
    local = sorted(finished - blocked)
    return Cleanup(tuple(remove), tuple(local), tuple(kept))
