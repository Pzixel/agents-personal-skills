#!/usr/bin/env python3
"""SessionStart worktree reaper: removes worktrees and branches merged as-is.

Narrow workflow around tools/worktree_policy.decide_cleanup: observe every
GitHub main checkout under ~/Documents/Repos, decide, execute mechanically.
A failing command skips its repository. `--dry-run` logs "would" lines only.
"""

import fcntl
import json
import os
import re
import subprocess
import sys
import time

_PLUGIN_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
TOOLS_DIR = os.path.realpath(
    _PLUGIN_TOOLS if os.path.isdir(_PLUGIN_TOOLS) else os.path.expanduser("~/.claude/tools")
)
sys.path.insert(0, TOOLS_DIR)

from worktree_policy import Worktree, decide_cleanup  # noqa: E402

REPOS_ROOT = os.path.expanduser("~/Documents/Repos")
TMP = os.path.expanduser("~/.claude/tmp")
LOCK = os.path.join(TMP, "worktree-reaper.lock")
LOG = os.path.join(TMP, "worktree-reaper.log")
GITHUB = re.compile(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$")


class Failed(Exception):
    pass


def log(line):
    with open(LOG, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {line}\n")


def run(args, cwd, timeout=60):
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as e:
        raise Failed(f"{' '.join(args)}: {e}") from e
    if p.returncode != 0:
        raise Failed(f"{' '.join(args)}: exit {p.returncode}: {p.stderr.strip()[:300]}")
    return p.stdout


def busy_cwds():
    p = subprocess.run(["lsof", "-d", "cwd", "-Fn"], capture_output=True, text=True, timeout=60)
    return {line[1:] for line in p.stdout.splitlines() if line.startswith("n")}


def is_busy(path, cwds):
    real = os.path.realpath(path)
    return any(c == real or c.startswith(real + os.sep) for c in cwds)


def github_repos():
    for name in sorted(os.listdir(REPOS_ROOT)):
        path = os.path.join(REPOS_ROOT, name)
        if not os.path.isdir(os.path.join(path, ".git")):
            continue
        try:
            url = run(["git", "config", "--get", "remote.origin.url"], path).strip()
        except Failed:
            continue
        m = GITHUB.search(url)
        if m:
            yield path, m.group(1)


def refs(repo, prefix, strip):
    out = run(["git", "for-each-ref", f"--format=%(refname:lstrip={strip}) %(objectname)", prefix], repo)
    return dict(line.split(" ", 1) for line in out.splitlines() if line)


def default_branch(repo, slug):
    try:
        return run(["git", "symbolic-ref", "refs/remotes/origin/HEAD"], repo).strip().rsplit("/", 1)[-1]
    except Failed:
        out = run(["gh", "repo", "view", slug, "--json", "defaultBranchRef"], repo)
        return json.loads(out)["defaultBranchRef"]["name"]


def worktrees(repo, cwds):
    result, blocks = [], run(["git", "worktree", "list", "--porcelain"], repo).strip().split("\n\n")
    for i, block in enumerate(blocks):
        fields = dict((line.split(" ", 1) + [""])[:2] for line in block.splitlines())
        if "prunable" in fields:
            continue
        path = fields["worktree"]
        branch = fields.get("branch", "").removeprefix("refs/heads/") or None
        dirty = bool(run(["git", "status", "--porcelain"], path).strip())
        result.append(Worktree(path, branch, fields.get("HEAD", ""), i == 0, dirty, is_busy(path, cwds)))
    return result


def reap(repo, slug, cwds, dry):
    name = os.path.basename(repo)
    run(["git", "fetch", "--prune", "-q", "origin"], repo, timeout=60)
    default = default_branch(repo, slug)
    pulls = json.loads(run(["gh", "pr", "list", "--repo", slug, "--state", "merged", "--limit", "500",
                            "--json", "headRefName,headRefOid"], repo))
    merged = {}
    for pr in pulls:
        merged.setdefault(pr["headRefName"], set()).add(pr["headRefOid"])
    target = f"refs/remotes/origin/{default}"
    into_default = set(run(["git", "for-each-ref", "--format=%(refname:lstrip=2)", "--merged", target,
                            "refs/heads"], repo).split())
    first_parent = set(run(["git", "rev-list", "--first-parent", target], repo).split())
    c = decide_cleanup(worktrees(repo, cwds), refs(repo, "refs/heads", 2), merged, default,
                       into_default, first_parent)
    for path, reason in c.kept:
        log(f"{name} keep {path}: {reason}")
    verb = "would " if dry else ""
    for path in c.remove_worktrees:
        log(f"{name} {verb}remove worktree {path}")
        if not dry:
            try:
                if os.path.isdir(path):
                    run(["git", "worktree", "remove", path], repo)
            except Failed as e:
                log(f"{name} keep {path}: git refused: {e}")
                return
    if not dry:
        run(["git", "worktree", "prune"], repo)
    for b in c.delete_local_branches:
        log(f"{name} {verb}delete local branch {b}")
        if not dry:
            # a concurrent session may have deleted it already; gone counts as done
            if refs(repo, f"refs/heads/{b}", 2):
                run(["git", "branch", "-D", b], repo)

def main():
    dry = "--dry-run" in sys.argv[1:]
    os.makedirs(TMP, exist_ok=True)
    os.chdir(TMP)  # our own cwd must not mark a worktree busy
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    cwds = busy_cwds()
    for repo, slug in github_repos():
        try:
            reap(repo, slug, cwds, dry)
        except (Failed, KeyError, ValueError) as e:
            log(f"{os.path.basename(repo)} skip repo: {e}")


if __name__ == "__main__":
    main()
