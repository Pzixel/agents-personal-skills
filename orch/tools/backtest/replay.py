"""Replay recorded Bash calls through the loop-cost gate.

usage: python3 replay.py <loop.tsv> <control.jsonl> [variants]
       python3 replay.py outcomes <calls.jsonl> [<start_utc of one call to probe>]
       python3 replay.py background-watch [days]
       python3 replay.py next-step [days] [--against <backtest stops.tsv>]
       python3 replay.py merge-gate [days]
       python3 replay.py plan-form <days> --policy <base gate_policy.py> [--labels <risk-labels.tsv>]
            (--policy: gate_policy.py inside a checkout of the base, e.g. a git worktree; a lone copy fails to load)
       python3 replay.py lever [days] [--judge] [--cap N]
       python3 replay.py ask-spend [days] [--judge] [--out <tsv>]   (default 28 days, ask-spend.tsv)
       python3 replay.py ask-spend-pins <pins.jsonl> [k]

loop.tsv: start_utc, duration_s, agent, agent_type, command (one looping
session, all rows one repository). control.jsonl: rows of extract.py (every
Bash call of 14 days, all repositories). Calls are replayed in time order: a
launch is decided at its start against the journal, its row is appended at its
end. A denied launch is assumed acknowledged (an ack row is written, the run
proceeds), which prices the gate. A control denial is a false fire when its key
is only multiplexer CLIs (MUX) or the command asks for --help; a key rule, not
a per-row reading.

Without arguments after the paths the real policy (gate_policy.loop_cost_decision)
runs. With `variants` three trigger (a) variants are modelled side by side with
the real identity and journal rows: perkey (minutes of the key since its ack),
pooled (minutes of the repository since any ack), aprime (pooled and the key
ran long twice since its ack); trigger (b) is the real one in all three."""
import collections
import csv
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import gate_policy as g  # noqa: E402
import transcript  # noqa: E402

MUX = {"gh", "kubectl", "docker", "git", "infra.py", "history.py", "secretctl", "perl", "cargo"}
_repos = {}


def ep(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def repo_of(d):
    if d not in _repos:
        out = subprocess.run(["git", "-C", d, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                             capture_output=True, text=True) if os.path.isdir(d) else None
        _repos[d] = os.path.dirname(out.stdout.strip()) if out and out.returncode == 0 else d
    return _repos[d]


def load(loop_path, control_path):
    csv.field_size_limit(10 ** 9)
    loop = [dict(r, duration_s=float(r["duration_s"] or 0), rp="loop",
                 agent_type="" if r["agent"] == "main" else "sub")
            for r in csv.DictReader(open(loop_path), delimiter="\t")]
    control = []
    with open(control_path) as fh:
        for line in fh:
            r = json.loads(line)
            r["rp"] = repo_of(r["repo"])
            control.append(r)
    return loop, control


def model(mode, live, key):
    acks = [r for r in live if r.get("ack")]
    longs = [r for r in live if not r.get("ack")]
    any_ack = max((r["t"] for r in acks), default=-1)
    own_ack = max((r["t"] for r in acks if r["key"] == key), default=-1)
    own = [r for r in longs if r["key"] == key and r["t"] > own_ack]
    pool = sum(r["duration_ms"] for r in longs if r["t"] > any_ack)
    if mode == "perkey":
        a = sum(r["duration_ms"] for r in own) >= 1800e3
    elif mode == "pooled":
        a = pool >= 1800e3 and any(r["key"] == key for r in longs)
    else:
        a = pool >= 1800e3 and len(own) >= 2
    return "a" if a else "b" if len(own) >= 3 else None


def run(rows, mode):
    events = sorted([(ep(r["start_utc"]), 0, i) for i, r in enumerate(rows)]
                    + [(ep(r["start_utc"]) + r["duration_s"], 1, i) for i, r in enumerate(rows)])
    journals = collections.defaultdict(list)
    out = []
    for t, kind, i in events:
        r = rows[i]
        journal = journals[r["rp"]] = [x for x in journals[r["rp"]] if x["t"] >= t - 21600]
        cmd = r["command"]
        if kind == 1:
            row = g.loop_cost_row(cmd, r["duration_s"] * 1000, r["agent_type"], t)
            if row:
                journal.append(dict(row, t=t))
            continue
        key = g.command_identity(cmd)
        if mode == "policy":
            d = g.loop_cost_decision(journal, cmd, t, r["agent_type"])
            trig = d["decision"] == "deny" and ("a" if "reached the budget" in d["reason"] else "b")
        else:
            trig = g.loop_cost_reads_journal(cmd) and model(mode, journal, key)
        if trig:
            out.append({"repo": r["rp"], "ts": g.loop_cost_iso(t), "key": key, "trig": trig, "command": cmd[:160]})
            journal.append({"t": t, "ts": g.loop_cost_iso(t), "key": key, "ack": "replay", "duration_ms": 0})
    return out


def false_fire(o):
    return "--help" in o["command"] or set(o["key"].split("+")) <= MUX


def outcomes(calls_path, probe_ts=None):
    """Outcomes gate over every call of extract.py: fires per trigger class and
    per day, the 10 most recent fires (the launched program, then 300 chars of the command), and the decision on the call at probe_ts."""
    rows = sorted((json.loads(l) for l in open(calls_path)), key=lambda r: r["start_utc"])
    fires = []
    for r in rows:
        d = g.outcomes_decision(r["command"], r.get("run_in_background"))
        if d["decision"] == "deny":
            fires.append(dict(r, trigger=d["trigger"], launch=d["launch"]))
        if probe_ts and r["start_utc"] == probe_ts:
            print("probe", probe_ts, d["decision"], d["trigger"])
    print("calls", len(rows), "fires", len(fires),
          "by trigger", dict(collections.Counter(f["trigger"] for f in fires)),
          "by agent", dict(collections.Counter("sub" if f["agent_type"] else "main" for f in fires)))
    for day, n in sorted(collections.Counter(f["start_utc"][:10] for f in fires).items()):
        print(day, n)
    for f in fires[-10:]:
        print(f["start_utc"], f["trigger"], "launch:", f["launch"][:120], "|", f["command"][:300].replace("\n", " "))


# Before the shared transcript.LAUNCH_RE: its generic alternatives also matched hook
# denials that mention the background (bogus id `E`).
_OLD_LAUNCH_RE = re.compile(
    r"(?:Command running in background with ID: ([A-Za-z0-9_-]+)"
    r"|[^\n]*?moved to (?:the )?background[^\n]*?ID:?\s*([A-Za-z0-9_-]+)"
    r"|[^\n]*?background[^\n]*?with ID:?\s+([A-Za-z0-9_-]+))",
    re.IGNORECASE,
)


def main_transcripts(days, skip_dir=None):
    """Main-session transcripts of every account modified in the last ``days`` days, minus the
    project directories ``skip_dir`` matches."""
    cutoff = time.time() - days * 86400
    roots = [os.path.expanduser("~/.claude/projects")] + glob.glob(
        os.path.expanduser("~/.claude-accounts/*/projects"))
    paths = set()
    for root in roots:
        for path in glob.glob(os.path.join(root, "*", "*.jsonl")):
            if os.path.basename(path).startswith("agent-") or os.path.getmtime(path) < cutoff:
                continue
            if skip_dir and skip_dir.search(os.path.basename(os.path.dirname(path))):
                continue
            paths.add(os.path.realpath(path))
    return sorted(paths)


def background_watch(days):
    """Replay gate_policy.background_watch_decision at every recorded turn end
    (`turn_duration`) of the main-session transcripts modified in ``days`` days."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    import gate_policy
    import transcript
    paths = main_transcripts(days)
    n = collections.Counter()
    fired = []
    for path in sorted(paths):
        entries = transcript.entries_of(path)
        old_only = [i for i, (raw, e) in enumerate(entries) if e.get("type") == "user" and any(
            b.get("type") == "tool_result" and _OLD_LAUNCH_RE.match(transcript.result_text(b).strip())
            and not transcript.launch_id(b) for b in transcript.blocks_of(e))]
        for facts in transcript.background_watch_scan(entries, gate_policy.BACKGROUND_WATCH_MARKER):
            if not facts["timestamp"]:
                continue  # end of file: no recorded stop
            n["turn ends"] += 1
            start, end = facts["turn_start"], facts["index"]
            if any(start <= i < end for i in old_only):
                n["turn ends with a denial the old launch regex took for a launch"] += 1
            if any(not t["closed"] and t["launch"] >= start for t in facts["tasks"].values()):
                n["turn ends with an open task launched this turn"] += 1
            d = gate_policy.background_watch_decision(dict(facts, prior_block_seen=False))
            if any(facts["tasks"][w]["launch"] >= start for w in d["waits"]):
                n["exempt as a pure wait"] += 1
            if d["decision"] != "block":
                continue
            n["fires"] += 1
            this_turn = [t for t in d["ids"] if facts["tasks"][t]["launch"] >= start]
            n["fires (a) launched this turn" if this_turn else "fires (b) watch ended this turn"] += 1
            if any(m["armed"] > facts["tasks"][t]["launch"] for t in d["ids"]
                   for m in facts["monitors"].values()):
                n["fires where a Monitor was armed after the launch and ended"] += 1
            fired.append((facts["timestamp"][:16], os.path.basename(path)[:8], ",".join(d["ids"])))
    for key, value in n.items():
        print(key, value)
    for row in sorted(fired):
        print(*row)


# Harness-driven subject runs (text evals, case replays, graders): not owner sessions. The
# CASE-61 backtest's prefixes plus two harness directories it missed.
_SUBJECT_DIR_RE = re.compile(
    r"-retro-evals-tmp-|-replay(?:-|$)|^-private-var-folders-|grade-pack|^-private-tmp-rl-headless|^-private-tmp-tier-live-")
# Witness stops of CASE-61: (session prefix, stop minute UTC, expected candidate).
_NEXT_STEP_WITNESSES = (
    ("c56ffb38", "2026-11-03T10:20", True), ("c56ffb38", "2026-11-03T10:44", True),
    ("29acce51", "2026-10-28T17:18", True), ("e0df5fca", "2026-10-23T18:07", False),
    ("f9af8046", "2026-10-24T00:40", False), ("5b6476f0", "2026-11-04T23:38", True),
)


def _entry_text(entry):
    return "\n".join(str(b.get("text") or "") for b in transcript.blocks_of(entry) if b.get("type") == "text")


def _owner_message(entry):
    if entry.get("type") != "user" or entry.get("isMeta"):
        return False
    if any(b.get("type") == "tool_result" for b in transcript.blocks_of(entry)):
        return False
    text = _entry_text(entry).strip()
    return bool(text) and not text.startswith(("Stop hook", "<", "This session is being continued"))


def first_stops(entries):
    """Index of the first stop of every turn: the last assistant text before the turn's first
    `Stop hook feedback:` entry, else its last assistant text. A turn ends at `turn_duration`,
    or, in a file without one, before each owner message."""
    ends = [i for i, (_, e) in enumerate(entries)
            if e.get("type") == "system" and e.get("subtype") == "turn_duration"]
    if not ends:
        last = None
        for i, (_, e) in enumerate(entries):
            if e.get("type") == "assistant" and _entry_text(e).strip():
                last = i
            elif _owner_message(e) and last is not None:
                ends.append(last)
                last = None
        if last is not None:
            ends.append(last)
    start = 0
    for end in ends:
        texts = [i for i in range(start, end + 1)
                 if entries[i][1].get("type") == "assistant" and _entry_text(entries[i][1]).strip()]
        if texts:
            stops = [texts[-1]]
            for i in range(start, end + 1):
                if entries[i][1].get("type") == "user" and _entry_text(entries[i][1]).lstrip().startswith(
                        "Stop hook feedback:"):
                    before = [t for t in texts if t < i]
                    if before:
                        stops.append(before[-1])
            yield min(stops)
        start = end + 1


def next_step(days, against=None):
    """Replay the hook's next-step candidate selection (trigger (a) and condition (c), no look-ahead:
    the session's state at the stop, an agent's finish read from its own transcript up to the stop)
    at the first stop of every turn in the window. (b) is the judge's question C: the candidates are
    the set a judge probe runs on. With ``against`` (the CASE-61 backtest's stops.tsv), every
    difference from its look-ahead (a) and not (c) set is listed with this replay's reason."""
    cut = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).isoformat()
    n = collections.Counter()
    rows = {}
    for path in main_transcripts(days, _SUBJECT_DIR_RE):
        entries = transcript.entries_of(path)
        session = os.path.basename(path)[:8]
        for stop in first_stops(entries):
            stamp = str(entries[stop][1].get("timestamp") or "")
            if stamp < cut:
                continue
            n["turn ends"] += 1
            sentence = g.next_step_sentence(_entry_text(entries[stop][1]))
            if not sentence:
                continue
            n["(a) matched"] += 1
            facts = None
            for facts in transcript.background_watch_scan(entries[: stop + 1], "\x00"):
                pass
            live = g.live_mechanisms(facts)
            n["exempt by (c)" if live else "candidates (a) and not (c)"] += 1
            rows[(session, stop)] = (stamp[:16], live, sentence)
    for key, value in n.items():
        print(key, value)
    print("candidates per day:", dict(sorted(collections.Counter(
        r[0][:10] for r in rows.values() if not r[1]).items())))
    for (session, stop), (stamp, live, sentence) in rows.items():
        if not live:
            print("candidate", stamp, session, stop, sentence[:110])
    for session, minute, expected in _NEXT_STEP_WITNESSES:
        hits = [(k, r) for k, r in rows.items() if k[0] == session and r[0] == minute]
        got = "candidate" if any(not r[1] for _, r in hits) else (
            "exempt by (c): " + ",".join(hits[0][1][1])[:80] if hits else "no (a) match")
        print("witness", session, minute, "expected", "candidate" if expected else "no candidate", "->", got)
    if against:
        with open(against) as fh:
            theirs = {(r["session"], int(r["idx"])): r for r in csv.DictReader(fh, delimiter="\t")
                      if r["t_stop"] >= cut[:16]}
        def free(r):
            return r["a"] == "1" and not any(r[k] == "1" for k in ("monitor_or_cron", "open_task", "unresolved_agent"))
        mine = {k for k, r in rows.items() if not r[1]}
        lookahead = {k for k, r in theirs.items() if free(r)}
        print("backtest (a) and not (c) with look-ahead:", len(lookahead), "this replay:", len(mine))
        for key in sorted(lookahead - mine):
            reason = ("exempt here, live: " + ",".join(rows[key][1])[:80]) if key in rows else "no (a) match or outside the replayed files"
            print("delta only-backtest", theirs[key]["t_stop"], *key, "v2 fire" if theirs[key]["fires_v2"] == "1" else "", reason)
        for key in sorted(mine - lookahead):
            reason = ("backtest exempted by look-ahead (c)" if key in theirs else "not in the backtest's rows")
            print("delta only-replay", rows[key][0], *key, reason)


def _transcript_files(days):
    """(path, caller type, delegate) of every main transcript of the last ``days`` days and its subagents
    (type from the sibling .meta.json)."""
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    mains = main_transcripts(days)
    files = [(path, "main", False) for path in mains]
    for path in mains:
        for sub in glob.glob(os.path.join(path[: -len(".jsonl")], "subagents", "agent-*.jsonl")):
            if os.path.getmtime(sub) < cutoff.timestamp():
                continue
            try:
                with open(sub[: -len(".jsonl")] + ".meta.json") as fh:
                    kind = json.load(fh).get("agentType") or "unknown"
            except (OSError, ValueError):
                kind = "unknown"
            files.append((sub, kind, True))
    return files


def _session_prefix(path):
    session = re.search(r"([0-9a-f]{8})[0-9a-f-]*(?:\.jsonl|/subagents)", path)
    return session.group(1) if session else os.path.basename(path)


def _entries_since(path, days):
    cut = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    for _, entry in transcript.entries_of(path):
        ts = entry.get("timestamp") or ""
        if ts >= cut:
            yield ts, entry


def _bash_blocks(entry):
    if entry.get("type") != "assistant":
        return
    for b in transcript.blocks_of(entry):
        if b.get("type") == "tool_use" and b.get("name") == "Bash":
            yield b.get("id"), str((b.get("input") or {}).get("command") or "")


def _user_text(entry):
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    return "\n".join(str(b.get("text") or "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def _bash_calls(days):
    """Every Bash call of the last ``days`` days: (stamp, session prefix, caller type, delegate, command),
    main sessions (delegate=False) and subagents/agent-*.jsonl (delegate=True)."""
    for path, kind, delegate in _transcript_files(days):
        sid = _session_prefix(path)
        for ts, entry in _entries_since(path, days):
            for _, cmd in _bash_blocks(entry):
                yield ts, sid, kind, delegate, cmd


def merge_gate(days):
    """Replay gate_policy.merge_gate_decision over every Bash call of the last ``days``
    days (``_bash_calls``). Prints per caller type allowed / refused by
    fields / refused as delegate / delegate fall-through (the phrase without a
    landing at command position, sent to the field check), each delegate refusal
    and each fall-through row."""
    counts = collections.defaultdict(collections.Counter)
    refusals, fell = [], []
    for ts, sid, kind, delegate, cmd in _bash_calls(days):
        d = g.merge_gate_decision(cmd, None, delegate)
        row = (ts[:19], sid, kind, cmd.replace("\n", " ")[:100])
        if d["decision"] == "allow":
            counts[kind]["allowed"] += 1
        elif d.get("kind") == "delegate":
            counts[kind]["refused-as-delegate"] += 1
            refusals.append(row)
        else:
            counts[kind]["refused-by-fields"] += 1
        if delegate and g._MERGE_RE.search(cmd) and not g.is_landing(cmd):
            counts[kind]["fell-through"] += 1
            fell.append(row)
    cols = ("allowed", "refused-by-fields", "refused-as-delegate", "fell-through")
    print("caller type | " + " | ".join(cols))
    for kind in sorted(counts, key=lambda k: (k != "main", k)):
        print(kind + " | " + " | ".join(str(counts[kind][c]) for c in cols))
    sub = [k for k in counts if k != "main"]
    print("total subagents | " + " | ".join(str(sum(counts[k][c] for k in sub)) for c in cols))
    print("\ndelegate refusals (time, session, caller type, command[:100]):")
    for row in sorted(refusals):
        print(" | ".join(row))
    print("\ndelegate rows sent to the field check (time, session, caller type, command[:100]):")
    for row in sorted(fell):
        print(" | ".join(row))


# CASE-72 L1504: the secretctl apply-k8s call that pruned 58 of 100 backend-env keys.
_SECRET_WRITE_WITNESS = ("beb0e057", "2026-11-08T00:13:38")
_SECRET_WRITE_TEXT_RE = re.compile(r"secretctl\b[^\n]*\bapply-k8s\b|kubectl\b[^\n]*\bsecrets?\b", re.I)


def secret_write(days):
    """Replay the deny-destructive decision over every Bash call of the last ``days`` days that names a
    Secret through kubectl or runs secretctl apply-k8s (or that the head policy denies with the Secret-write
    reason); list each with its decision, fires per day and the CASE-72 witness."""
    params = g.load_params()
    rows = []
    for ts, sid, kind, delegate, cmd in _bash_calls(days):
        if not _SECRET_WRITE_TEXT_RE.search(cmd) and "secretctl" not in cmd:
            continue
        d = g.destructive_decision(cmd, "", None, "", params, "", None, False, None)
        secret_hit = g._SECRET_WRITE_REASON in d.get("reason", "")
        if secret_hit or _SECRET_WRITE_TEXT_RE.search(cmd):
            rows.append((ts[:19], sid, kind, d["decision"] + "/" + d["class"], secret_hit, cmd.replace("\n", " ")[:140]))
    rows.sort()
    fires = [r for r in rows if r[4]]
    print("calls naming a Secret or secretctl apply-k8s", len(rows), "Secret-write fires", len(fires),
          "fires/day %.2f" % (len(fires) / days))
    print("fires per day:", dict(sorted(collections.Counter(r[0][:10] for r in fires).items())))
    for r in rows:
        print("fire" if r[4] else "pass", *r[:4], r[5])
    session, stamp = _SECRET_WRITE_WITNESS
    hit = [r for r in rows if r[1] == session and r[0] == stamp]
    print("witness", session, stamp, "expected fire", "actual", ("fire" if hit[0][4] else "pass " + hit[0][3]) if hit else "not found")


def _load_base(base_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("gate_policy_base", base_path)
    base = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(base)
    return base


def wrapper_tails(days, base_path):
    """Every Bash call running a wrapper of ``g._WRAPPER_TOOLS`` with `--` in the last ``days`` days, decided
    by the gate_policy at ``base_path`` and by the head; the changed decisions grouped by (base -> head, reason
    head), each listed."""
    base = _load_base(base_path)
    params = g.load_params()
    wrapped = re.compile(r"(?:^|[\s;&|(/])(?:%s)\b[^\n]*\s--(?:\s|$)" % "|".join(map(re.escape, g._WRAPPER_TOOLS)))
    total, changed = 0, collections.defaultdict(list)
    for ts, sid, kind, delegate, cmd in _bash_calls(days):
        if not wrapped.search(cmd):
            continue
        total += 1
        b = base.destructive_decision(cmd, "", None, "", params, "", None, False, None)
        h = g.destructive_decision(cmd, "", None, "", params, "", None, False, None)
        if (b["decision"], b["class"]) != (h["decision"], h["class"]):
            key = "%s/%s -> %s/%s | %s" % (b["decision"], b["class"], h["decision"], h["class"],
                                           h["reason"].split(";")[0].split(":")[0][:70])
            changed[key].append((ts[:19], sid, kind, cmd.replace("\n", " ")[:140]))
    print("wrapped calls", total, "changed", sum(map(len, changed.values())))
    for key, rows in sorted(changed.items(), key=lambda kv: -len(kv[1])):
        print("group", len(rows), key)
    for key, rows in sorted(changed.items()):
        for row in sorted(rows):
            print("  ", key.split(" | ")[0], *row)


_APPROVE_LINE = re.compile(r"^\s*APPROVE\s+([0-9a-fA-F]{64})\s*$", re.M)
_HASH_LITERAL = re.compile(r"sha256=([0-9a-fA-F]{64})\b")
# Arguments: a word after `(bash|sh|zsh) <path>` on the marker line that is not a redirect, operator or comment.
_PLAN_ARGS = re.compile(r"(?:^|[\s;&|(])(?:bash|sh|zsh)\s+\S+\s+(?![#>&|;<]|\d*>)\S")
# The marker line as the base regex read it (any \\s, any non-blank path): the narrowed path class must keep them.
_LOOSE_PLAN_LINE = re.compile(r"^\s*(?:bash|sh|zsh)\s+\S+\s+#\s*DESTRUCTIVE_PLAN\s+sha256=[0-9a-fA-F]{64}\s*$")
# Arguments of a wrapper launch (python3 -c '...execvp("bash", ...)' <plan>.sh b): a word after a shell-file
# word on the marker line that is not a redirect, operator or comment.
_FILE_ARGS = re.compile(r"\S\.(?:sh|zsh|bash)\s+(?![#>&|;<]|\d*>)\S")
_SHELL_FILE = re.compile(r"(?:^|[\s;&|({])(?:bash|sh|zsh)\s+(?:-[a-z]+\s+)*([^\s;&|<>'\"-][^\s;&|<>'\"]*)")


def plan_form(days, base_path, labels=None):
    """Every distinct Bash call (by tool_use id; a session copy repeats ids) carrying the plan token in the last
    ``days`` days, base vs head: counts of exact-form, off-form launch and mention rows, and of changed rows per
    kind. No plan text is passed, so the replay shows the form rule, not the approval compare. For off-form
    launches it also prints: an earlier typed `APPROVE` of the same hash in the session (yes / none / non-literal
    hash), arguments by ``_PLAN_ARGS`` and the launches with no shell-runs-a-file match on the marker line;
    token-less runs (main session) of a file the session launched with the token; and, with ``labels``
    (risk-labels.tsv: session, plan_path_tail, effect), the IRREVERSIBLE plans launched only off the form."""
    base = _load_base(base_path)
    params = g.load_params()
    counts, changed, approve, seen = collections.Counter(), collections.Counter(), collections.Counter(), set()
    args = loose = narrowed_out = 0
    no_shell = collections.Counter()
    plan_files, plain_runs, launches = collections.defaultdict(set), [], collections.defaultdict(list)
    for path, kind, delegate in _transcript_files(days):
        sid = _session_prefix(path)
        approved = set()
        for ts, entry in _entries_since(path, days):
            if entry.get("type") == "user" and not delegate:
                approved.update(h.lower() for h in _APPROVE_LINE.findall(_user_text(entry)))
                continue
            for bid, cmd in _bash_blocks(entry):
                if bid in seen:
                    continue
                seen.add(bid)
                if g._PLAN_TOKEN not in cmd:
                    if not delegate and ".sh" in cmd:
                        plain_runs.append((sid, ts, cmd))
                    continue
                shape = "exact" if g.plan_marker(cmd) else ("launch" if g._off_form_plan_launch(cmd) else "mention")
                counts[shape] += 1
                script = g._strip_comments(g._split_heredocs(cmd)[0])
                names = {os.path.basename(m.strip("\"'")) for m in _SHELL_FILE.findall(script)}
                if shape != "mention" and not delegate:
                    plan_files[sid].update(n for n in names if n.endswith((".sh", ".zsh", ".bash")))
                if shape != "mention":
                    launches[sid].append((shape, cmd))
                for line in cmd.split("\n"):
                    if _LOOSE_PLAN_LINE.match(line):
                        loose += 1
                        narrowed_out += not g._PLAN_MARKER_RE.match(line)
                if shape == "launch":
                    line = next((l for l in cmd.split("\n") if g._PLAN_TOKEN in l), "")
                    h = _HASH_LITERAL.search(line)
                    approve["non-literal hash" if not h else "yes" if h.group(1).lower() in approved else "none"] += 1
                    args += bool(_PLAN_ARGS.search(line.split("#")[0] + " #"))
                    if not _SHELL_FILE.search(line.split("#")[0]):
                        wrapped_args = bool(_FILE_ARGS.search(line.split("#")[0]))
                        no_shell["with arguments" if wrapped_args else "without"] += 1
                        print("no shell-file on the marker line", "with arguments" if wrapped_args else "without",
                              ts[:19], sid, cmd.replace("\n", " ")[:140])
                who = kind if delegate else ""
                b = base.destructive_decision(cmd, who, None, "", params, "", None, False, None)
                hd = g.destructive_decision(cmd, who, None, "", params, "", None, False, None)
                if (b["decision"], b["class"]) != (hd["decision"], hd["class"]):
                    key = (shape, b["decision"] + "/" + b["class"], hd["decision"] + "/" + hd["class"])
                    changed[key] += 1
                    print("changed", *key, ts[:19], sid, kind, cmd.replace("\n", " ")[:140])
    print("token calls", dict(counts), "changed", sum(changed.values()))
    for key, n in sorted(changed.items()):
        print("group", n, *key)
    print("plan lines in the base regex shape", loose, "not matching the head marker regex", narrowed_out)
    print("off-form launches: earlier typed APPROVE of the same hash", dict(approve))
    print("off-form launches with arguments (regex on the marker line)", args,
          "| no shell-runs-a-file match on the marker line", dict(no_shell),
          "| with arguments in total", args + no_shell["with arguments"])
    def runs_plan(sid, cmd):
        script = g._strip_comments(g._split_heredocs(cmd)[0])
        for name in plan_files[sid]:
            m = re.search(r"(?:^|[\s;&|({])(?:(?:/\S*/)?(?:bash|sh|zsh)|source|\.)\s+(?:-[a-z]+\s+)*(?!-)[^\s;&|<>]*"
                          + re.escape(name) + r"(?=[\s;&|<>\"']|$)", script)
            if m and not re.search(r"(?:bash|sh|zsh)\s+-n\b", m.group(0)):
                return True
        return False
    plain = [(sid, ts) for sid, ts, cmd in plain_runs if runs_plan(sid, cmd)]
    print("token-less runs of a file launched with the token", len(plain), *("%s %s" % (s, t[:19]) for s, t in plain))
    if labels:
        with open(labels) as fh:
            rows = [r for r in csv.DictReader(fh, delimiter="\t") if r["effect"] == "IRREVERSIBLE"]
        for r in rows:
            shapes = sorted({s for s, cmd in launches[r["session"]] if r["plan_path_tail"] in cmd})
            print("irreversible", r["session"], r["plan_path_tail"], "launch shapes", shapes or ["not found"],
                  "ONLY OFF-FORM" if shapes == ["launch"] else "")


# Witness stop of CASE-69: (session prefix, stop minute UTC, expected judge answer).
_LEVER_WITNESS = ("2ed7c202", "2026-11-06T08:21", "true")
LEVER_JUDGE_CAP = 200


def _hook_judge_args(name="JUDGE_ARGS"):
    """A constant of reply-lint.py (its judge command or timeout), read from its source: the hook
    runs on import."""
    import ast
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hooks", "reply-lint.py")
    for node in ast.parse(open(path).read()).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == name:
            return ast.literal_eval(node.value)
    raise SystemExit(name + " not found in " + path)


_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tmp", "judge-cache")


def _judge(policy, prompt, args, timeout, run_tag=""):
    """The hook's judge on one prompt: the parsed verdict, or "timeout" / "error". Verdicts are cached on
    disk by sha256(prompt, run_tag), so a rerun re-judges only changed prompts; another ``run_tag`` draws
    a fresh sample (the noise floor)."""
    import hashlib
    key = hashlib.sha256((run_tag + "\x00" + prompt).encode()).hexdigest()
    path = os.path.join(_CACHE_DIR, key + ".json")
    if os.path.isfile(path):
        with open(path) as fh:
            return json.load(fh)
    try:
        run = subprocess.run(args + [prompt], capture_output=True, text=True,
                             timeout=timeout, env=dict(os.environ, REPLY_LINT_JUDGE="1"), cwd="/tmp")
    except subprocess.TimeoutExpired:
        return "timeout"
    verdict = policy.parse_judge_verdict(run.stdout)
    if verdict is None:
        return "error"
    os.makedirs(_CACHE_DIR, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(verdict, fh)
    return verdict


# The first prompt of a retro-evals subject session: not an owner session.
_SUBJECT_PROMPT = "You are the agent in the situation described in the file"


def _subject_session(entries):
    first = next((e for _, e in entries if _owner_message(e)), None)
    return first is not None and _entry_text(first).lstrip().startswith(_SUBJECT_PROMPT)


def _owner_stops(days):
    """(path, entries, stop index, stamp) of the first stop of every turn of the owner sessions in the window."""
    cut = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).isoformat()
    for path in main_transcripts(days, _SUBJECT_DIR_RE):
        entries = transcript.entries_of(path)
        if _subject_session(entries):
            continue
        for stop in first_stops(entries):
            stamp = str(entries[stop][1].get("timestamp") or "")
            if stamp >= cut:
                yield path, entries, stop, stamp


def _facts_at(entries, stop):
    facts = None
    for facts in transcript.background_watch_scan(entries[: stop + 1], "\x00"):
        pass
    return facts


def _lever_candidate(text, entries, stop):
    return g.lever_candidate(text)


def _timed_step_candidate(text, entries, stop):
    sentence = g.timed_step_sentence(text)
    return sentence and g.exempt_candidates("", sentence, _facts_at(entries, stop))[1]


# (candidate function, judge_prompt keyword arguments for a candidate, verdict key, witnesses).
_CANDIDATE_MODES = {
    "lever": (_lever_candidate, lambda c: {"lever": c}, "lever", [_LEVER_WITNESS]),
    "timed-step": (_timed_step_candidate, lambda c: {"timed_step": c}, "timed_step",
                   [("beb0e057", "2026-11-02T10:39", "true"), ("beb0e057", "2026-11-07T23:49", "true")]),
}


def candidate_mode(name, days, judge=False, cap=LEVER_JUDGE_CAP):
    """Replay one reply-lint trigger at the first stop of every turn of the owner sessions in the window
    (the text trigger first, the transcript scan only on a match); with ``judge`` run the hook's judge on
    each candidate, at most the newest ``cap``, and list every fire."""
    from concurrent.futures import ThreadPoolExecutor
    candidate_fn, kwargs_fn, key, witnesses = _CANDIDATE_MODES[name]
    n = collections.Counter()
    rows = []
    for path, entries, stop, stamp in _owner_stops(days):
        n["turn ends"] += 1
        text = _entry_text(entries[stop][1])
        candidate = candidate_fn(text, entries, stop)
        if candidate:
            n["already judged by needs_judge"] += g.needs_judge(text)
            if name == "timed-step":
                n["of them through a relative marker"] += bool(g._RELATIVE_TIME_RE.search(candidate))
            rows.append([stamp[:16], os.path.basename(path)[:8], text, candidate, "-"])
    rows.sort()
    n["candidates"] = len(rows)
    if judge:
        args, timeout = _hook_judge_args(), _hook_judge_args("JUDGE_TIMEOUT_S")

        def answer(row):
            verdict = _judge(g, g.judge_prompt(row[2], **kwargs_fn(row[3])), args, timeout)
            return verdict if isinstance(verdict, str) else str(verdict[key]).lower()
        with ThreadPoolExecutor(8) as pool:
            newest = rows[-cap:]
            for row, got in zip(newest, pool.map(answer, newest)):
                row[4] = got
        n["judged"] = min(len(rows), cap)
        n["judge true"] = sum(r[4] == "true" for r in rows)
    for k, value in n.items():
        print(k, value)
    print("candidates per day:", dict(sorted(collections.Counter(r[0][:10] for r in rows).items())))
    if judge:
        print("judge true per day:", dict(sorted(collections.Counter(r[0][:10] for r in rows if r[4] == "true").items())))
    for stamp, session, text, candidate, got in rows:
        print("candidate", session, stamp, got, re.sub(r"\s+", " ", candidate.strip())[:160])
    for session, minute, expected in witnesses:
        actual = next((r[4] if judge else "candidate" for r in rows if r[1] == session and r[0] == minute), "not a candidate")
        print("witness", session, minute, "expected", expected if judge else "candidate", "actual", actual)


def lever(days, judge=False, cap=LEVER_JUDGE_CAP):
    return candidate_mode("lever", days, judge, cap)


_CONTROL_KEYS = ("handoff", "deferral", "next_step", "lever")


def judge_control(days, cap, policy_path, out, run_tag=""):
    """The A-D judge control: the newest ``cap`` first stops the head hook would judge, candidates from the
    head triggers, judged with the prompt of the gate_policy at ``policy_path`` (base or head); one TSV row
    per stop with the four booleans."""
    import importlib.util
    import inspect
    from concurrent.futures import ThreadPoolExecutor
    spec = importlib.util.spec_from_file_location("gate_policy_under_test", policy_path)
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    has_e = "timed_step" in inspect.signature(policy.judge_prompt).parameters
    stops = []
    for path, entries, stop, stamp in _owner_stops(days):
        text = _entry_text(entries[stop][1])
        lever_c, next_c, timed_c = g.lever_candidate(text), g.next_step_sentence(text), g.timed_step_sentence(text)
        if next_c or timed_c:
            next_c, timed_c = g.exempt_candidates(next_c, timed_c, _facts_at(entries, stop))
        if next_c or lever_c or timed_c or g.needs_judge(text):
            stops.append((stamp[:19], os.path.basename(path)[:8], stop, text, next_c, lever_c, timed_c))
    stops = sorted(stops)[-cap:]
    args, timeout = _hook_judge_args(), _hook_judge_args("JUDGE_TIMEOUT_S")

    def one(row):
        extra = {"timed_step": row[6]} if has_e else {}
        return _judge(policy, policy.judge_prompt(row[3], row[4], row[5], **extra), args, timeout, run_tag)
    with ThreadPoolExecutor(8) as pool, open(out, "w") as fh:
        fh.write("stamp\tsession\tstop\t" + "\t".join(_CONTROL_KEYS) + "\n")
        for row, verdict in zip(stops, pool.map(one, stops)):
            cells = [verdict] * 4 if isinstance(verdict, str) else [str(verdict[k]).lower() for k in _CONTROL_KEYS]
            fh.write("\t".join([row[0], row[1], str(row[2])] + cells) + "\n")
    print("judged stops", len(stops), "->", out)


def _ask_calls(days):
    """(session, line, stamp, project, tool_input) of every AskUserQuestion call of the owner sessions in
    the window, one per tool_use id."""
    cut = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).isoformat()
    seen = set()
    for path in main_transcripts(days, _SUBJECT_DIR_RE):
        project = os.path.basename(os.path.dirname(path))
        with open(path, errors="replace") as fh:
            for number, line in enumerate(fh, 1):
                if '"AskUserQuestion"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                stamp = str(entry.get("timestamp") or "")
                content = (entry.get("message") or {}).get("content")
                for block in content if isinstance(content, list) and stamp >= cut else []:
                    if (isinstance(block, dict) and block.get("type") == "tool_use"
                            and block.get("name") == "AskUserQuestion" and block.get("id") not in seen):
                        seen.add(block.get("id"))
                        yield os.path.basename(path)[:8], number, stamp, project, block.get("input") or {}


def _spend_judge(tool_input, run_tag=""):
    """The hook's spend judge on one input, cached on disk by sha256(prompt, run_tag): the verdict dict,
    or the runner's error string."""
    import hashlib
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hooks"))
    import model_judge
    import importlib.util
    spec = importlib.util.spec_from_file_location("ask_gate", os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "hooks", "ask-existing-gate.py"))
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    prompt = g.spend_judge_prompt(tool_input)
    path = os.path.join(_CACHE_DIR, "spend-" + hashlib.sha256((run_tag + "\x00" + prompt).encode()).hexdigest())
    if os.path.isfile(path):
        with open(path) as fh:
            return json.load(fh)
    verdict, error = model_judge.run(hook.JUDGE_ARGS, prompt, hook.JUDGE_TIMEOUT_S, hook.JUDGE_ENV,
                                     g.parse_spend_verdict)
    if verdict is None:
        return error
    os.makedirs(_CACHE_DIR, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(verdict, fh)
    return verdict


# Wider than the prefilter on purpose: a number next to something we could pay for. A call it catches and
# the prefilter does not gets a row with prefilter=false, so a spend outside the prefilter is visible.
_WIDE_SPEND_RE = re.compile(
    r"\d[^|]{0,40}(?:домен|ящик|сервер|диск|прокси|лиценз|тариф|domain|mailbox|inbox|server|disk|prox|licen|seat"
    r"|vcpu)|(?:домен|ящик|сервер|диск|прокси|лиценз|domain|mailbox|server|disk|seat|vcpu)[^|]{0,40}\d",
    re.I,
)


def ask_spend(days, out, judge=False):
    """Replay the spend rule over every AskUserQuestion call of the window: prefilter hits, and with
    ``judge`` the hook's judge and decision on each hit. One TSV row per prefilter hit (fires and
    non-fires, amount or not) and per call only the wide scan catches, for hand labels; totals and fires
    per day per project on stdout."""
    from concurrent.futures import ThreadPoolExecutor
    calls = list(_ask_calls(days))
    hits = [c for c in calls if g.spend_candidate(c[4])]
    wide = [c for c in calls if not g.spend_candidate(c[4])
            and any(_WIDE_SPEND_RE.search(t) for t in g._ask_texts(c[4]))]
    verdicts = list(ThreadPoolExecutor(8).map(lambda c: _spend_judge(c[4]), hits)) if judge else [None] * len(hits)
    fires, failed, per_project = 0, 0, collections.Counter()
    with open(out, "w") as fh:
        fh.write("session\tline\tstamp\tproject\tprefilter\tamount\tverdict\tfired\tsentence\ttext\n")
        for call, verdict in list(zip(hits, verdicts)) + [(c, "-") for c in wide]:
            ok = isinstance(verdict, dict)
            failed += judge and verdict != "-" and not ok
            fired = g.ask_existing_decision(call[4], verdict if ok else None) == g._ASK_SPEND_REASON
            fires += fired
            per_project[call[3]] += fired
            text = " ".join(" | ".join(g._ask_texts(call[4])).split())
            amount = any(g._CURRENCY_RE.search(t) for t in g._ask_texts(call[4]))
            cells = [call[0], str(call[1]), call[2][:19], call[3], str(verdict != "-").lower(), str(amount).lower(),
                     str(verdict["spend"]).lower() if ok else str(verdict), str(fired).lower(),
                     verdict["sentence"] if ok else "", text[:600]]
            fh.write("\t".join(" ".join(c.split()) for c in cells) + "\n")
    print("calls", len(calls), "prefilter", len(hits), "wide only", len(wide), "judge failed", failed,
          "fires", fires, "fires/day %.2f" % (fires / float(days)), "->", out)
    for project, n in per_project.most_common():
        if n:
            print("  ", project, n)


def ask_spend_pins(spec_path, k=3):
    """Judge each pinned input ``k`` times with fresh draws: spec rows are JSON lines
    {"name", "expect": "fire"|"pass", "input"}; prints the decision per draw."""
    with open(spec_path) as fh:
        pins = [json.loads(line) for line in fh if line.strip()]
    for pin in pins:
        got = []
        for draw in range(k):
            verdict = _spend_judge(pin["input"], run_tag="pin-%d" % draw)
            ok = isinstance(verdict, dict)
            reason = g.ask_existing_decision(pin["input"], verdict if ok else None)
            got.append("fire" if reason == g._ASK_SPEND_REASON else ("pass" if ok else "error:" + str(verdict)))
        print(pin["name"], "expect", pin["expect"], "got", " ".join(got))


def judge_diff(a, b):
    """Per-stop differences of the four booleans between two judge-control TSVs."""
    def load_tsv(path):
        with open(path) as fh:
            return {(r["session"], r["stop"]): r for r in csv.DictReader(fh, delimiter="\t")}
    left, right = load_tsv(a), load_tsv(b)
    common = sorted(set(left) & set(right))
    print("common stops", len(common), "only left", len(set(left) - set(right)), "only right", len(set(right) - set(left)))
    for k in _CONTROL_KEYS:
        diffs = [s for s in common if left[s][k] != right[s][k]]
        print(k, "differ", len(diffs), "true left", sum(left[s][k] == "true" for s in common),
              "true right", sum(right[s][k] == "true" for s in common))
        for s in diffs:
            print("  ", k, left[s]["stamp"], *s, left[s][k], "->", right[s][k])


def main():
    if sys.argv[1] == "lever":
        args = sys.argv[2:]
        cap = int(args[args.index("--cap") + 1]) if "--cap" in args else LEVER_JUDGE_CAP
        return lever(int(args[0]) if args and args[0].isdigit() else 28, "--judge" in args, cap)
    if sys.argv[1] == "timed-step":
        args = sys.argv[2:]
        cap = int(args[args.index("--cap") + 1]) if "--cap" in args else LEVER_JUDGE_CAP
        return candidate_mode("timed-step", int(args[0]) if args and args[0].isdigit() else 14, "--judge" in args, cap)
    if sys.argv[1] == "judge-control":
        args = sys.argv[2:]
        opt = {k: args[args.index(k) + 1] for k in ("--cap", "--policy", "--out", "--run") if k in args}
        return judge_control(int(args[0]) if args and args[0].isdigit() else 14, int(opt.get("--cap", 300)),
                             opt.get("--policy", g.__file__), opt["--out"], opt.get("--run", ""))
    if sys.argv[1] == "ask-spend":
        args = sys.argv[2:]
        if "--out" in args and args.index("--out") + 1 >= len(args):
            sys.exit("usage: python3 replay.py ask-spend [days] [--judge] [--out <tsv>]")
        out = args[args.index("--out") + 1] if "--out" in args else "ask-spend.tsv"
        return ask_spend(int(args[0]) if args and args[0].isdigit() else 28, out, "--judge" in args)
    if sys.argv[1] == "ask-spend-pins":
        return ask_spend_pins(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 3)
    if sys.argv[1] == "judge-diff":
        return judge_diff(sys.argv[2], sys.argv[3])
    if sys.argv[1] == "next-step":
        args = sys.argv[2:]
        against = args[args.index("--against") + 1] if "--against" in args else None
        days = int(args[0]) if args and args[0].isdigit() else 14
        return next_step(days, against)
    if sys.argv[1] == "secret-write":
        return secret_write(int(sys.argv[2]) if len(sys.argv) > 2 else 28)
    if sys.argv[1] == "plan-form":
        labels = sys.argv[sys.argv.index("--labels") + 1] if "--labels" in sys.argv else None
        return plan_form(int(sys.argv[2]), sys.argv[sys.argv.index("--policy") + 1], labels)
    if sys.argv[1] == "wrapper-tails":
        return wrapper_tails(int(sys.argv[2]), sys.argv[sys.argv.index("--policy") + 1])
    if sys.argv[1] == "merge-gate":
        return merge_gate(int(sys.argv[2]) if len(sys.argv) > 2 else 28)
    if sys.argv[1] == "background-watch":
        return background_watch(int(sys.argv[2]) if len(sys.argv) > 2 else 14)
    if sys.argv[1] == "outcomes":
        return outcomes(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    loop, control = load(sys.argv[1], sys.argv[2])
    modes = ("perkey", "pooled", "aprime") if sys.argv[3:] == ["variants"] else ("policy",)
    print("variant | control denials | on experiment scripts | false fires | max false/day"
          " | loop denials | loop first denial | loop first (a)")
    for mode in modes:
        c, lp = run(control, mode), run(loop, mode)
        f = [o for o in c if false_fire(o)]
        per_day = collections.Counter(o["ts"][:10] for o in f)
        print(" | ".join(str(x) for x in (
            mode, len(c), len(c) - len(f), len(f), max(per_day.values(), default=0), len(lp),
            lp[0]["ts"] if lp else "-", next((o["ts"] for o in lp if o["trig"] == "a"), "-"))))


if __name__ == "__main__":
    main()
