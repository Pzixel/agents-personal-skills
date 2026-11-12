"""Pure decisions of tmp-evict: which first-level entries of repository tmp directories are moved aside
(evict), deleted after the grace period (purge), or moved back because they were used again (restore), and
how transcript references join to them.

No I/O: facts, the current time and the thresholds are arguments."""

import collections
import datetime
import os
import re
import shlex

DAY = 86400.0
RECENT_TRANSCRIPT_S = 24 * 3600  # a unit named by a transcript written this recently is never evicted or purged
LISTING_MAX = 20
SESSIONS_MAX = 20
EVICTED_DIR = ".evicted"
EVICTED_TSV = ".evicted.tsv"
NOT_UNITS = (EVICTED_DIR, EVICTED_TSV)
DELETING = ("purging", "purge-failed")  # row states of an entry whose deletion started

# A path token that contains a `tmp/` component (the extraction of the reference measurement).
TOKEN = re.compile(r"[\w.@+~/$-]*\btmp/[\w.@+/-]+")
# A quoted string holding a `tmp/` component; it may contain spaces, which TOKEN cuts.
QUOTED = re.compile(r"(['\"])([^'\"\n]*\btmp/[^'\"\n]*)\1")
# A `/tmp/x` match right after `)` or `}`, or after a closing quote or backtick, continues a prefix the text does
# not resolve: `$(pwd)/tmp/x`, `${X}/tmp/x`, `"$X"/tmp/x`, `` `cmd`/tmp/x ``. An opening quote (`"/tmp/x"`) is the
# global /tmp.
CLOSERS = set(")}")
QUOTES = set("\"'`")
OPENING_AFTER = set(" \t\n=(:,[")


def _opaque_prefix(text, start):
    before = text[start - 1] if start else ""
    if before in CLOSERS:
        return True
    return before in QUOTES and start >= 2 and text[start - 2] not in OPENING_AFTER

# path: unit path; size: bytes; files: file count; activity: latest max(mtime, ctime) inside, None when the walk
# failed; last_ref: latest transcript reference (epoch) or None; sessions: referencing session ids; keep: holds
# KEEP; recent_ref: named by a transcript whose file was modified within RECENT_TRANSCRIPT_S; tracked: holds a
# git-tracked path; link: the unit itself is a symbolic link.
Unit = collections.namedtuple("Unit", "path size files activity last_ref sessions keep recent_ref tracked link")
# path: entry under <tmp>/.evicted; original: path it was evicted from; evicted_at: epoch or None (no row);
# original_free: nothing exists at original; state: the open row's state (see open_evictions) or None.
Evicted = collections.namedtuple(
    "Evicted", "path original evicted_at activity last_ref recent_ref original_free state")
# kind: evict | purge | restore | held (record a permanent hold) | hold (log only)
Decision = collections.namedtuple("Decision", "kind path reason")


def decide(units, evicted, now, cfg, complete=True):
    """Every action of one pass. cfg: big_bytes, big_idle_days, small_idle_days, grace_days. complete: every
    transcript was read; otherwise nothing is evicted or newly purged this pass."""
    out = []
    for u in units:
        if u.link:
            out.append(Decision("hold", u.path, "a symbolic link unit is never moved"))
            continue
        if not complete or u.keep or u.tracked or u.activity is None or u.recent_ref:
            continue
        idle = (now - max(u.activity, u.last_ref or 0.0)) / DAY
        big = u.size >= cfg["big_bytes"]
        limit = cfg["big_idle_days"] if big else cfg["small_idle_days"]
        if idle > limit:
            out.append(Decision("evict", u.path, "idle %.1f d > %d d (%s)" % (
                idle, limit, ">= %d bytes" % cfg["big_bytes"] if big else "small")))
    for e in evicted:
        if e.evicted_at is None:
            out.append(Decision("hold", e.path, "no eviction row in " + EVICTED_TSV))
            continue
        if e.state == "held":
            continue
        if e.state in DELETING:
            # A deletion that stopped part-way changed the remainder's ctime: it is finished, never restored.
            out.append(Decision("purge", e.path, "retrying a deletion that stopped part-way"))
            continue
        if e.activity is None:
            continue
        if max(e.activity, e.last_ref or 0.0) > e.evicted_at:
            if e.original_free:
                out.append(Decision("restore", e.path, "used after eviction"))
            else:
                out.append(Decision("held", e.path, "used after eviction, but %s exists" % e.original))
        elif complete and not e.recent_ref and now - e.evicted_at > cfg["grace_days"] * DAY:
            out.append(Decision("purge", e.path, "evicted %.1f d ago, unused since" % ((now - e.evicted_at) / DAY)))
    return out


def exempt_named(decisions, named):
    """Evictions turned into holds for units a git-tracked file names. named: unit path -> the first tracked file
    naming `tmp/<name>`, or why git could not tell."""
    return [Decision("hold", d.path, "named by tracked " + named[d.path]) if d.kind == "evict" and d.path in named
            else d for d in decisions]


def tool_strings(value):
    """Every string inside a tool_use input."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from tool_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from tool_strings(v)


def _resolved(tok, cwd, home):
    if tok.startswith("~"):
        tok = home + tok[1:]
    if not tok.startswith("/"):
        if not cwd:
            return None
        tok = os.path.join(cwd, tok)
    return os.path.normpath(tok)


def refs_of_text(text, cwd, home):
    """("path", absolute normalized path) for each resolvable tmp token, ("name", <entry name>) for each token that
    is relative or whose prefix the text does not resolve: it names every known unit of that name, since the
    command may have changed directory first."""
    for m in TOKEN.finditer(text):
        tok = m.group(0).replace("${HOME}", "~").replace("$HOME", "~")
        name = tok.split("tmp/", 1)[1].split("/", 1)[0]
        opaque = "$" in tok or _opaque_prefix(text, m.start()) and tok.startswith("/tmp/")
        if (opaque or not tok.startswith(("/", "~"))) and name and name not in NOT_UNITS:
            yield ("name", name)
        if "$" in tok:
            continue
        path = _resolved(tok, cwd, home)
        if path:
            yield ("path", path)
    for m in QUOTED.finditer(text):
        inner = m.group(2)
        if "$" in inner or not any(c.isspace() for c in inner):
            continue  # TOKEN took it whole
        path = _resolved(inner, cwd, home)
        if path:
            yield ("path", path)


def refs_of_call(tool_input, cwd, home):
    """References of one tool call: its input strings, and its `cwd` when that lies inside a tmp entry."""
    for text in tool_strings(tool_input):
        yield from refs_of_text(text, cwd, home)
    if "/tmp/" in cwd:
        yield ("path", os.path.normpath(cwd))


def unit_keys(path, tmp_dirs):
    """Unit or evicted-entry paths ``path`` lies in. tmp_dirs: written or real tmp directory path -> canonical
    path. Every `tmp` component is tried, so a unit inside a nested checkout is found too."""
    parts = path.split("/")
    keys = []
    for i, part in enumerate(parts[:-1]):
        if part != "tmp":
            continue
        tmp = tmp_dirs.get("/".join(parts[:i + 1]))
        if tmp is None:
            continue
        name = parts[i + 1]
        if name == EVICTED_DIR and i + 2 < len(parts):
            keys.append(tmp + "/" + EVICTED_DIR + "/" + parts[i + 2])
        elif name not in NOT_UNITS:
            keys.append(tmp + "/" + name)
    return keys


def canonical(paths, tmp_dirs):
    """Path references keyed by the canonical unit or evicted-entry path they lie in (a reference written through
    an alias reaches the canonical key); a path in no known tmp directory keeps its own key."""
    out = {}
    for path, (stamp, sids) in paths.items():
        for key in unit_keys(path, tmp_dirs) or [path]:
            mine = out.setdefault(key, [0.0, set()])
            mine[0], mine[1] = max(mine[0], stamp), mine[1] | sids
    return out


def references(paths, names, recent, keys, name):
    """(latest reference or None, sessions, named by a recent transcript) of a unit known under the canonical
    ``keys`` and called ``name``. paths, names: key -> [epoch, session ids]; recent: (paths, names) of each
    recently modified transcript, paths canonical."""
    stamp, sids = 0.0, set()
    for seen in [paths.get(k) for k in keys] + [names.get(name)]:
        if seen:
            stamp, sids = max(stamp, seen[0]), sids | seen[1]
    hot = any(k in p for p, _ in recent for k in keys) or any(name in n for _, n in recent)
    return stamp or None, sids, hot


def _utc(epoch):
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)


def _stamp(epoch):
    return _utc(epoch).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _epoch(stamp):
    moment = datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S.%fZ")
    return moment.replace(tzinfo=datetime.timezone.utc).timestamp()


def _field(value):
    return str(value).replace("\t", " ").replace("\n", " ")


def row(epoch, event, original, moved, size, files, listing, sessions):
    """One `.evicted.tsv` line: time, event, original path, evicted path, bytes, files, first paths, sessions,
    and what the event means with the command that brings the entry back."""
    back = "to restore: mv %s %s" % (shlex.quote(moved), shlex.quote(original))
    how = {"pending": "eviction started; " + back,
           "evicted": back,
           "failed": "eviction failed; the entry stays at the original path",
           "held": "used after eviction while the original path is taken: kept here for good; " + back,
           "purging": "deletion started",
           "purge-failed": "deletion stopped part-way; the rest is deleted on the next pass",
           "restored": "restored to the original path",
           "purged": "deleted for good after the grace period"}[event]
    return "\t".join(_field(v) for v in (
        _stamp(epoch), event, original, moved, size, files,
        ",".join(listing[:LISTING_MAX]), ",".join(sorted(sessions)[:SESSIONS_MAX]), how)) + "\n"


def open_evictions(text):
    """Evicted path -> (original path, eviction epoch, state) for every entry not restored, purged or failed.
    state: pending (row written, rename not confirmed), evicted, held (kept for good), purging (deletion started),
    purge-failed (deletion stopped part-way)."""
    rows = {}
    for line in text.splitlines():
        cols = line.split("\t")
        if len(cols) < 4:
            continue
        try:
            epoch = _epoch(cols[0])
        except ValueError:
            continue
        event, moved = cols[1], cols[3]
        if event in ("pending", "evicted"):
            rows[moved] = (cols[2], epoch, event)
        elif event in ("failed", "restored", "purged"):
            rows.pop(moved, None)
        elif event in ("held", "purging", "purge-failed") and moved in rows:
            original, at, _ = rows[moved]
            rows[moved] = (original, at, event)
    return rows


def evicted_name(name, taken, epoch):
    """Name under .evicted/ for an entry called ``name``; a taken name gets a UTC time suffix."""
    if name not in taken:
        return name
    return name + "." + _utc(epoch).strftime("%Y%m%dT%H%M%S%fZ")


def should_start(stamp_mtime, now):
    """Whether a scheduled pass runs: no pass started within the last 24 hours."""
    return stamp_mtime is None or now - stamp_mtime >= DAY


def log_tail(text, max_bytes):
    """The newest whole lines of a log, at most ``max_bytes`` characters."""
    if len(text) <= max_bytes:
        return text
    tail = text[-max_bytes:]
    return tail[tail.find("\n") + 1:]
