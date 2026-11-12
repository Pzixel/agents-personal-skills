#!/usr/bin/env python3
"""Print part N of the worker rules: the instruction text an orch agent gets in
place of the user-level and project CLAUDE.md files it omits.

Sources, in order: the user-level CLAUDE.md (``$CLAUDE_CONFIG_DIR``, default
``~/.claude``) with its imports followed one level, then the CLAUDE.md at the
root of the repository holding the working directory, with imports followed
one level inside that root only. An import is a line that is wholly ``@`` and a
path naming an existing file; any other line starting with ``@`` is text. Each
file is cut at the installer's block markers; each segment drops its
owner-only regions when they are well formed and is delivered whole otherwise.
An absent file delivers nothing; a file that cannot be opened, or a project
file or import resolving outside the repository, delivers one notice line;
undecodable bytes arrive as U+FFFD, as the client reads them.

The text is cut at line boundaries into parts under ``PART_BYTES`` UTF-8
bytes. Part 1 starts with ``BEGIN``; every part ends with its own closing line,
so a part the client replaces by a preview, or a part beyond the commands the
skill runs, leaves a closing line missing and the agent's guard fires.
Always exits 0 and writes UTF-8 whatever the locale: a non-zero exit aborts
the agent's spawn.
"""

import os
import sys

BEGIN = "orch worker rules: begin"
CLOSING = "orch worker rules: part {n} of {m} end"
UNUSED = "orch worker rules: command {n} unused ({m} parts)"
FAILED = "orch worker rules: assembly failed: {error}"
SKIPPED = "orch worker rules: import skipped, outside the repository: {line}"
OUTSIDE = "orch worker rules: file skipped, outside the repository: {path}"
UNREADABLE = "orch worker rules: file not readable: {path}"
OWNER_BEGIN = "<!-- BEGIN owner only -->"
OWNER_END = "<!-- END owner only -->"
# The block install.sh writes into the Codex AGENTS.md (install.sh `begin`/`end`).
BLOCK_BEGIN = "<!-- BEGIN agents-personal-skills rules/always-on.md -->"
BLOCK_END = "<!-- END agents-personal-skills rules/always-on.md -->"
# One `!` command delivers up to 30,000 characters (measured); bytes bound
# characters and UTF-16 units from above, so the cut holds under either count.
PART_BYTES = 28000
CLOSING_RESERVE = 64


def segments(lines):
    """Lines cut at the installer's block marker lines; the marker lines stay
    with the segment they open or close."""
    out, cur = [], []
    for line in lines:
        if line == BLOCK_BEGIN and cur:
            out.append(cur)
            cur = []
        cur.append(line)
        if line == BLOCK_END:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def drop_owner_only(lines):
    """``lines`` without its owner-only regions, or unchanged when the region
    markers are not a well-formed sequence (BEGIN, END, BEGIN, END, ...)."""
    marks = [line for line in lines if line in (OWNER_BEGIN, OWNER_END)]
    if marks != [OWNER_BEGIN, OWNER_END] * (len(marks) // 2) or len(marks) % 2:
        return list(lines)
    out, inside = [], False
    for line in lines:
        if line == OWNER_BEGIN:
            inside = True
        elif line == OWNER_END:
            inside = False
        elif not inside:
            out.append(line)
    return out


def worker_lines(text):
    """The delivered lines of one file's text."""
    return [line for seg in segments(text.splitlines()) for line in drop_owner_only(seg)]


def import_target(line, base_dir, home):
    """The existing file an import line names (install.sh rule: line-start
    ``@``, ``\\ `` is a space, ``~/`` is the home directory), or None when
    the line is text: more than a path, or a path naming no file."""
    if not line.startswith("@") or len(line) < 2 or any(c.isspace() for c in line[1:].replace("\\ ", "")):
        return None
    ref = line[1:].replace("\\ ", " ")
    path = os.path.join(home, ref[2:]) if ref.startswith("~/") else os.path.join(base_dir, ref)
    return path if os.path.isfile(path) else None


def read(path):
    """The text of ``path``; None when absent; ``UNREADABLE`` when it exists
    but cannot be opened."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except FileNotFoundError:
        return None
    except OSError:
        return UNREADABLE.format(path=path)


def file_text(path, home, inside, seen):
    """Delivered lines of the file at ``path`` with its imports expanded one
    level. ``inside``: the directory the file and its imports must resolve
    into, or None. ``seen``: real paths already delivered."""
    real = os.path.realpath(path)
    if real in seen or not os.path.lexists(path):
        return []
    if inside is not None and not within(real, inside):
        return [OUTSIDE.format(path=path)]
    seen.add(real)
    text = read(path)
    if text is None:
        return []
    out = []
    for line in worker_lines(text):
        target = import_target(line, os.path.dirname(path), home)
        if target is None:
            out.append(line)
            continue
        real = os.path.realpath(target)
        if inside is not None and not within(real, inside):
            out.append(SKIPPED.format(line=line))
            continue
        if real in seen:
            continue
        seen.add(real)
        sub = read(target) or ""
        base = os.path.dirname(target)
        out.extend(line for line in worker_lines(sub) if import_target(line, base, home) is None)
    return out


def within(real, root):
    return real.startswith(os.path.realpath(root).rstrip("/") + "/")


def repo_root(cwd):
    d = os.path.realpath(cwd)
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def rules_lines(config_dir, cwd, home):
    seen = set()
    out = file_text(os.path.join(config_dir, "CLAUDE.md"), home, None, seen)
    root = repo_root(cwd)
    if root is not None:
        out += file_text(os.path.join(root, "CLAUDE.md"), home, root, seen)
    return out


def split_long(line, limit):
    """``line`` as pieces of at most ``limit`` UTF-8 bytes, cut at characters."""
    pieces, cur, size = [], [], 0
    for ch in line:
        b = len(ch.encode("utf-8"))
        if size + b > limit:
            pieces.append("".join(cur))
            cur, size = [], 0
        cur.append(ch)
        size += b
    pieces.append("".join(cur))
    return pieces


def parts(lines, limit=PART_BYTES):
    """The text ``BEGIN`` + ``lines`` as parts, each with its closing line and
    under ``limit`` UTF-8 bytes."""
    room = limit - CLOSING_RESERVE
    chunks, cur, size = [], [], 0
    for line in [BEGIN] + lines:
        for piece in split_long(line, room - 1):
            b = len(piece.encode("utf-8")) + 1
            if cur and size + b > room:
                chunks.append(cur)
                cur, size = [], 0
            cur.append(piece)
            size += b
    chunks.append(cur)
    m = len(chunks)
    return ["\n".join(c + [CLOSING.format(n=i, m=m)]) for i, c in enumerate(chunks, 1)]


def output(n, config_dir, cwd, home):
    ps = parts(rules_lines(config_dir, cwd, home))
    if n > len(ps):
        return UNUSED.format(n=n, m=len(ps))
    return ps[n - 1]


def main():
    try:
        try:
            n = int(sys.argv[1])
        except (IndexError, ValueError):
            n = 1
        home = os.path.expanduser("~")
        config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(home, ".claude")
        text = output(n, config_dir, os.getcwd(), home)
    except Exception as e:  # a broken assembly must reach the guard, not abort the spawn
        text = FAILED.format(error="%s: %s" % (type(e).__name__, e))
    try:
        sys.stdout.buffer.write((text + "\n").encode("utf-8"))
        sys.stdout.flush()
    except Exception:
        pass


if __name__ == "__main__":
    main()
