"""Observation behind a pinned plan run of hooks/deny-destructive.py: for each
file a plan pins and runs (gate_policy.plan_pinned_paths), read the file and
its blob on the local `origin/HEAD` tip of its repository, under one deadline
for all files. Returns facts only, the text that was hashed included (the plan floor reads it);
gate_policy._pin_verdict decides."""

import hashlib
import os
import re
import stat
import subprocess
import time

MAX_BYTES = 1 << 20
# Variables that would make `git -C <dir>` answer for another repository.
_REPO_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
             "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE")


def _git_env():
    env = {k: v for k, v in os.environ.items() if k not in _REPO_ENV}
    env.update(GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0", GIT_LITERAL_PATHSPECS="1")
    return env


def _read(path):
    """(real path, bytes) of a regular file of at most MAX_BYTES; stat before open, so a FIFO never blocks."""
    real = os.path.realpath(path)
    info = os.stat(real)
    if not stat.S_ISREG(info.st_mode):
        raise OSError("not a regular file")
    if info.st_size > MAX_BYTES:
        raise OSError("%d bytes" % info.st_size)
    with open(real, "rb") as fh:
        return real, fh.read(MAX_BYTES + 1)


def _observe_one(path, deadline, env):
    try:
        real, data = _read(path)
    except (OSError, ValueError) as exc:
        return {"error": "unreadable", "detail": str(exc)[:200]}
    if len(data) > MAX_BYTES:
        return {"error": "unreadable", "detail": "grew past %d bytes" % MAX_BYTES}

    def git(*args, stdin=None):
        left = deadline - time.monotonic()
        if left <= 0:
            raise RuntimeError("over the git time budget")
        try:
            done = subprocess.run(["git", "-C", os.path.dirname(real)] + list(args), input=stdin, env=env,
                                  capture_output=True, timeout=left)
        except subprocess.TimeoutExpired:
            raise RuntimeError("git %s timed out at the git time budget" % args[0])
        if done.returncode:
            raise RuntimeError(done.stderr.decode("utf-8", "replace").strip()[:200] or "git exit %d" % done.returncode)
        return done.stdout.decode("utf-8", "replace")

    try:
        head = git("for-each-ref", "--format=%(objectname)%00%(committerdate:iso-strict)%00%(symref:short)",
                   "refs/remotes/origin/HEAD").strip()
        if not head:
            raise RuntimeError("no refs/remotes/origin/HEAD")
        tip, tip_date, branch = head.split("\0")
        entry = git("ls-tree", "-z", tip, "--", os.path.basename(real)).rstrip("\0")
        mode, _, rest = entry.partition(" ")
        tree_blob = rest.partition(" ")[2].partition("\t")[0] or None
        file_blob = git("hash-object", "--stdin", stdin=data).strip()
        origin_url = _shown_url(git("config", "--get", "remote.origin.url").strip())
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
        return {"error": "git", "detail": str(exc)[:200]}
    return {"real_path": real, "sha256": hashlib.sha256(data).hexdigest(), "file_blob": file_blob,
            "tree_mode": mode or None, "tree_blob": tree_blob, "origin_url": origin_url,
            "tip": tip, "tip_date": tip_date, "branch": branch, "text": data.decode("utf-8", "replace")}


def _shown_url(url):
    """The origin URL as the owner may see it: no userinfo (a token), no control character
    (a line break would forge a second facts line)."""
    if re.search(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]", url):
        raise RuntimeError("remote.origin.url holds a control character")
    return re.sub(r"^([a-zA-Z][a-zA-Z0-9+.-]*://)[^/?#]*@", r"\1", url)


def observe(paths, budget_s=2.0):
    """{path: facts} for each path; one deadline over every git call."""
    deadline, env = time.monotonic() + budget_s, _git_env()
    return {path: _observe_one(path, deadline, env) for path in paths}
