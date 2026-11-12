"""tmp-evict [--apply | --scheduled | --list-keep | enable | disable] [--projects DIR]...

Two-phase cleanup of repository tmp directories (<repo>/tmp, <repo>/.worktrees/*/tmp). A first-level entry
idle (no write, no ctime change, no tool-call reference in any Claude Code transcript) for more than 14 days
when it holds 100 MB or more, else 180 days, is moved to <tmp>/.evicted/ with a row in <tmp>/.evicted.tsv
holding its `mv` restore line; it is deleted 14 days later unless it was used again, in which case it is moved
back. Never touched: a directory holding a file named KEEP, an entry holding a git-tracked file, an entry whose
`tmp/<name>` a git-tracked file of its repository names, an entry that is a symbolic link, and a tmp directory
that is a symbolic link. A reference is a tool-call input naming the entry (a relative or unresolvable
`tmp/<name>` names every entry of that name) or a tool call whose cwd lies inside it.
Thresholds: orchestrate-params.json `tmp_evict`.

Repositories are the git checkouts of the `cwd` values in the transcripts of $CLAUDE_CONFIG_DIR/projects and
~/.claude/projects (subagent transcripts included); a separate transcript store of another account is read only
when passed with --projects. Not seen: Codex sessions, people and other programs, glob and parent-directory
references. Such a reader that finds an entry gone does not bring it back: the `.evicted.tsv` row holds the `mv`
line that does, for the 14 days of grace.

Without an argument (or with --dry-run): dry run, prints the plan and changes nothing. --apply: the real pass. --scheduled: the
SessionStart form: runs --apply detached at most once per 24 hours and only after `tmp-evict enable` (marker
~/.claude/tmp-evict.enabled; `tmp-evict disable` removes it); its log is ~/.claude/tmp/tmp-evict.log.
--list-keep: KEEP directories, eviction candidates named by a tracked file, and entries held for good, with
their size.
"""

import argparse
import fcntl
import glob
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import gate_policy  # noqa: E402
import tmp_evict_policy as P  # noqa: E402

MARKER = os.path.expanduser("~/.claude/tmp-evict.enabled")
STATE = os.path.expanduser("~/.claude/tmp")
LOG = os.path.join(STATE, "tmp-evict.log")
LOG_MAX = 512 * 1024


def log(line):
    print(time.strftime("%Y-%m-%dT%H:%M:%S ") + line, flush=True)


def stores(extra):
    roots = [os.environ.get("CLAUDE_CONFIG_DIR") or "", os.path.expanduser("~/.claude")]
    paths = [os.path.join(r, "projects") for r in roots if r] + list(extra)
    return sorted({os.path.realpath(p) for p in paths if os.path.isdir(p)})


def _timestamp(value):
    for stamp in (str(value), str(value)[:19] + ".0Z"):
        try:
            return P._epoch(stamp)
        except ValueError:
            pass
    return None


def scan_file(path):
    """(path, file mtime, {path: [ts, sids]}, {name: [ts, sids]}, cwds, error) of one transcript."""
    paths, names, cwds = {}, {}, set()
    home = os.path.expanduser("~")
    fallback = os.path.basename(path)[:-6]
    try:
        mtime = os.stat(path).st_mtime
        with open(path, errors="replace") as fh:
            for number, line in enumerate(fh):
                head = number < 50 and not cwds
                if not head and ("tmp/" not in line or '"tool_use"' not in line):
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict):
                    continue
                cwd = str(entry.get("cwd") or "")
                if cwd:
                    cwds.add(cwd)
                if entry.get("type") != "assistant":
                    continue
                stamp, sid = _timestamp(entry.get("timestamp")), str(entry.get("sessionId") or fallback)
                message = entry.get("message")
                content = message.get("content") if isinstance(message, dict) else None
                for block in content if isinstance(content, list) else []:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    for kind, value in P.refs_of_call(block.get("input"), cwd, home):
                        seen = (paths if kind == "path" else names).setdefault(value, [0.0, set()])
                        seen[0] = max(seen[0], stamp or 0.0)
                        seen[1].add(sid)
    except FileNotFoundError:
        return path, None, {}, {}, set(), None  # removed by retention after the glob
    except OSError as e:
        return path, None, {}, {}, set(), str(e)
    return path, mtime, paths, names, cwds, None


def gather(projects):
    """Merged transcript facts, or an error string when none can be trusted."""
    files = sorted({f for root in stores(projects) for f in glob.glob(root + "/**/*.jsonl", recursive=True)})
    if not files:
        return None, "no transcripts found"
    facts = {"paths": {}, "names": {}, "cwds": set(), "errors": [], "recent": [], "files": len(files)}
    with multiprocessing.Pool(min(8, os.cpu_count() or 1)) as pool:
        for path, mtime, paths, names, cwds, error in pool.imap_unordered(scan_file, files, chunksize=20):
            if error:
                facts["errors"].append(path + ": " + error)
            facts["cwds"] |= cwds
            for mine, theirs in ((facts["paths"], paths), (facts["names"], names)):
                for key, (stamp, sids) in theirs.items():
                    seen = mine.setdefault(key, [0.0, set()])
                    seen[0] = max(seen[0], stamp)
                    seen[1] |= sids
            if mtime and time.time() - mtime < P.RECENT_TRANSCRIPT_S:
                facts["recent"].append((paths, names))
    return facts, None


def main_checkouts(cwds):
    mains = set()
    for cwd in sorted(cwds):
        if not os.path.isdir(cwd) or any(cwd == m or cwd.startswith(m + "/") for m in mains):
            continue
        try:
            out = subprocess.run(["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                 capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            continue
        common = out.stdout.strip().rstrip("/")
        if out.returncode == 0 and os.path.basename(common) == ".git":
            mains.add(os.path.dirname(common))
    return sorted(mains)


def tmp_directories(mains):
    """Written path -> canonical (real) path of every repository tmp directory. A tmp directory reached through a
    symbolic link (the directory itself or its worktree) is skipped whole: its target is not a repository tmp."""
    found = {}
    for main in mains:
        real_main = os.path.realpath(main)
        for tmp in [main + "/tmp"] + sorted(glob.glob(glob.escape(main) + "/.worktrees/*/tmp")):
            if not os.path.isdir(tmp):
                continue
            real = os.path.realpath(tmp)
            if real != real_main + tmp[len(main):]:
                log("skip %s: a symbolic link to %s" % (tmp, real))
                continue
            found[tmp] = found[real] = real
    return found


def tracked_names(checkout):
    """First-level names under tmp/ that hold a git-tracked path; None when git cannot tell."""
    try:
        out = subprocess.run(["git", "-C", checkout, "ls-files", "-z", "--", "tmp"],
                             capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return {p.split(b"/")[1].decode(errors="replace") for p in out.stdout.split(b"\0") if p.count(b"/") >= 1}


def walk(path):
    """(bytes, files, latest max(mtime, ctime), first paths) of an entry, symlinks not followed; None on error."""
    def raise_(e):
        raise e
    try:
        st = os.lstat(path)
        size, files, activity, listing = 0, 0, max(st.st_mtime, st.st_ctime), []
        if not os.path.isdir(path) or os.path.islink(path):
            return st.st_size, 1, activity, [os.path.basename(path)]
        for d, dirs, names in os.walk(path, onerror=raise_):
            dirs.sort()
            for name in dirs:
                st = os.lstat(os.path.join(d, name))
                activity = max(activity, st.st_mtime, st.st_ctime)
            for name in sorted(names):
                st = os.lstat(os.path.join(d, name))
                activity = max(activity, st.st_mtime, st.st_ctime)
                size, files = size + st.st_size, files + 1
                if len(listing) < P.LISTING_MAX:
                    listing.append(os.path.relpath(os.path.join(d, name), path))
        return size, files, activity, listing
    except OSError:
        return None


def tracked_reference(checkout, name):
    """The first git-tracked file of ``checkout`` naming `tmp/<name>`, why git could not tell, or None."""
    try:
        out = subprocess.run(["git", "-C", checkout, "grep", "-l", "-F", "-e", "tmp/" + name],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return "file unknown: git grep failed: %s" % e
    if out.returncode == 1 and not out.stderr:
        return None
    if out.returncode != 0:
        return "file unknown: git grep failed: " + out.stderr.strip()[:200]
    return "file " + out.stdout.splitlines()[0]


def collect(facts):
    """Units, evicted entries and per-path walk facts of every discovered tmp directory."""
    tmp_dirs = tmp_directories(main_checkouts(facts["cwds"]))
    paths = P.canonical(facts["paths"], tmp_dirs)
    recent = [(P.canonical(p, tmp_dirs), n) for p, n in facts["recent"]]
    units, evicted, walked = [], [], {}
    for tmp in sorted(set(tmp_dirs.values())):
        tracked = tracked_names(os.path.dirname(tmp))
        if tracked is None:
            log("skip %s: git ls-files failed" % tmp)
            continue
        try:
            names = sorted(os.listdir(tmp))
        except OSError as e:
            log("skip %s: %s" % (tmp, e))
            continue
        for name in names:
            if name in P.NOT_UNITS:
                continue
            path = tmp + "/" + name
            walked[path] = walk(path)
            w = walked[path]
            link = os.path.islink(path)
            last, sids, hot = P.references(paths, facts["names"], recent, [path], name)
            units.append(P.Unit(path, w[0] if w else 0, w[1] if w else 0, w[2] if w else None, last, sids,
                                os.path.isfile(path + "/KEEP") and not link, hot, name in tracked, link))
        try:
            with open(tmp + "/" + P.EVICTED_TSV) as fh:
                rows = P.open_evictions(fh.read())
        except FileNotFoundError:
            rows = {}
        except OSError as e:
            log("skip evicted entries of %s: %s" % (tmp, e))
            continue
        evicted_dir = tmp + "/" + P.EVICTED_DIR
        for name in sorted(os.listdir(evicted_dir)) if os.path.isdir(evicted_dir) else []:
            path = evicted_dir + "/" + name
            original, at, state = rows.get(path, (None, None, None))
            walked[path] = walk(path)
            w = walked[path]
            last, sids, hot = P.references(paths, facts["names"], recent, [path] + ([original] if original else []),
                                           os.path.basename(original or name))
            evicted.append(P.Evicted(path, original, at, w[2] if w else None, last, hot,
                                     bool(original) and not os.path.lexists(original), state))
    return units, evicted, walked, tmp_dirs


def is_git_tmp(tmp):
    parent = os.path.dirname(tmp)
    if os.path.lexists(parent + "/.git"):
        return True
    grand = os.path.dirname(parent)
    return os.path.basename(grand) == ".worktrees" and os.path.lexists(os.path.dirname(grand) + "/.git")


def safe_target(path, container, tmp):
    """Why acting on ``path`` (an entry of ``container``, inside repository tmp ``tmp``) is refused, or None."""
    if os.path.realpath(os.path.dirname(path)) != container or os.path.realpath(tmp) != tmp or not is_git_tmp(tmp):
        return "real parent is not a repository tmp directory"
    real, why = gate_policy.own_removal(path)
    if why:
        return why
    return None if real == path else "resolves to " + real


def unchanged(path, before):
    now = walk(path)
    return before is not None and now is not None and now[2] <= before[2]


def act(decision, units, evicted, walked):
    """Execute one decision; returns the log line."""
    path = decision.path
    if decision.kind == "hold":
        return "hold %s: %s" % (path, decision.reason)
    if decision.kind == "evict":
        u = units[path]
        tmp = os.path.dirname(path)
        why = safe_target(path, tmp, tmp)
        if why or not unchanged(path, walked[path]):
            return "keep %s: %s" % (path, why or "changed since the scan")
        target = tmp + "/" + P.EVICTED_DIR
        os.makedirs(target, exist_ok=True)
        moved = target + "/" + P.evicted_name(os.path.basename(path), set(os.listdir(target)), time.time())
        w = walked[path]
        # The pending row comes first, so no entry sits under .evicted/ without a row: an eviction never confirmed
        # reads as used after its pending time (the rename sets the entry's ctime) and moves back.
        _append(tmp, P.row(time.time(), "pending", path, moved, w[0], w[1], w[3], u.sessions))
        try:
            os.rename(path, moved)
        except OSError:
            _append(tmp, P.row(time.time(), "failed", path, moved, "", "", [], set()))
            raise
        at = max(time.time(), os.lstat(moved).st_ctime)
        _append(tmp, P.row(at, "evicted", path, moved, w[0], w[1], w[3], u.sessions))
        return "evicted %s -> %s (%d bytes): %s" % (path, moved, w[0], decision.reason)
    e = evicted[path]
    tmp = os.path.dirname(os.path.dirname(path))
    if decision.kind == "held":
        _append(tmp, P.row(time.time(), "held", e.original, path, "", "", [], set()))
        return "held %s for good: %s" % (path, decision.reason)
    if decision.kind == "restore":
        if os.path.lexists(e.original):
            return "hold %s: %s exists" % (path, e.original)
        os.rename(path, e.original)
        _append(tmp, P.row(time.time(), "restored", e.original, path, "", "", [], set()))
        return "restored %s -> %s" % (path, e.original)
    why = safe_target(path, os.path.dirname(path), tmp)
    retry = e.state in P.DELETING
    if why or not retry and not unchanged(path, walked[path]):
        return "keep %s: %s" % (path, why or "changed since the scan")
    w = walked[path]
    if not retry:
        _append(tmp, P.row(time.time(), "purging", e.original, path, w[0], w[1], [], set()))
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)
    except OSError:
        if e.state != "purge-failed":
            _append(tmp, P.row(time.time(), "purge-failed", e.original, path, "", "", [], set()))
        raise
    _append(tmp, P.row(time.time(), "purged", e.original, path, w[0], w[1], [], set()))
    return "purged %s (%d bytes): %s" % (path, w[0], decision.reason)


def _append(tmp, line):
    with open(tmp + "/" + P.EVICTED_TSV, "a") as fh:
        fh.write(line)


def plan(units, evicted, now, params, complete):
    """(decisions, unit path -> tracked file naming it) of one pass: one `git grep` per eviction candidate."""
    decisions = P.decide(units, evicted, now, params, complete)
    named = {}
    for d in decisions:
        if d.kind == "evict":
            why = tracked_reference(os.path.dirname(os.path.dirname(d.path)), os.path.basename(d.path))
            if why:
                named[d.path] = why
    return P.exempt_named(decisions, named), named


def run(args):
    params = gate_policy.load_params().get("tmp_evict")
    if not params:
        log("stop: orchestrate-params.json has no tmp_evict section")
        return 1
    started = time.time()
    facts, error = gather(args.projects)
    if error:
        log("stop: " + error)
        return 1
    units, evicted, walked, tmp_dirs = collect(facts)
    if facts["errors"]:
        for line in facts["errors"][:20]:
            log("unreadable transcript " + line)
        log("no evict or new purge this pass: %d transcripts unreadable" % len(facts["errors"]))
    decisions, named = plan(units, evicted, time.time(), params, not facts["errors"])
    if args.list_keep:
        for u in units:
            if u.keep:
                print("%12d  KEEP  %s" % (u.size, u.path))
        for path, why in sorted(named.items()):
            print("%12d  named by tracked %s  %s" % (walked[path][0], why, path))
        for e in evicted:
            if e.state == "held":
                w = walked.get(e.path)
                print("%12d  held for good (original %s)  %s" % (w[0] if w else 0, e.original, e.path))
        return 0
    by_unit = {u.path: u for u in units}
    by_entry = {e.path: e for e in evicted}
    log("scanned %d transcripts, %d tmp directories, %d units, %d evicted entries in %.1f s" % (
        facts["files"], len(set(tmp_dirs.values())), len(units), len(evicted), time.time() - started))
    totals = {}
    for d in decisions:
        w = walked.get(d.path)
        size = w[0] if w else 0
        count = totals.setdefault(d.kind, [0, 0])
        count[0], count[1] = count[0] + 1, count[1] + size
        if args.apply:
            try:
                log(act(d, by_unit, by_entry, walked))
            except OSError as e:
                log("failed %s %s: %s" % (d.kind, d.path, e))
        else:
            unit = by_unit.get(d.path)
            idle = (time.time() - max(unit.activity, unit.last_ref or 0)) / P.DAY if unit else 0
            print("would %-7s %12d bytes  idle %6.1f d  %s  (%s)" % (d.kind, size, idle, d.path, d.reason))
    for kind, (n, size) in sorted(totals.items()):
        log("%s %s: %d entries, %d bytes" % ("done" if args.apply else "plan", kind, n, size))
    if not args.apply and os.path.isfile(LOG):
        with open(LOG) as fh:
            lines = fh.read().splitlines()
        print("last scheduled pass: " + (lines[-1] if lines else "none"))
    return 0


def scheduled_start(marker, state, now):
    """Open lock file when this scheduled pass should run, else None (not enabled, ran within 24 h, or
    another pass holds the lock)."""
    if not os.path.isfile(marker):
        return None
    os.makedirs(state, exist_ok=True)
    lock = open(state + "/tmp-evict.lock", "w")
    stamp = state + "/tmp-evict.last-run"
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    if not P.should_start(os.path.getmtime(stamp) if os.path.exists(stamp) else None, now):
        lock.close()
        return None
    with open(stamp, "w"):
        pass
    return lock


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", choices=["enable", "disable"])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="the default: print the plan, change nothing")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--scheduled", action="store_true")
    mode.add_argument("--list-keep", action="store_true", help="KEEP units, units named by tracked files, entries held for good")
    parser.add_argument("--projects", action="append", default=[])
    args = parser.parse_args()
    if args.command == "enable":
        os.makedirs(os.path.dirname(MARKER), exist_ok=True)
        with open(MARKER, "w") as fh:
            fh.write("tmp-evict runs from SessionStart at most once per 24 h; `tmp-evict disable` stops it.\n")
        print("enabled: " + MARKER)
        return 0
    if args.command == "disable":
        if os.path.exists(MARKER):
            os.remove(MARKER)
        print("disabled")
        return 0
    if not args.scheduled:
        return run(args)
    lock = scheduled_start(MARKER, STATE, time.time())
    if lock is None:
        return 0
    try:
        with open(LOG, "a+") as fh:
            fh.seek(0)
            kept = P.log_tail(fh.read(), LOG_MAX)
            fh.seek(0)
            fh.truncate()
            fh.write(kept)
        sys.stdout = sys.stderr = open(LOG, "a")
        args.apply = True
        return run(args)
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        lock.close()
