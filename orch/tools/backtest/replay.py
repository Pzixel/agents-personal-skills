"""Replay recorded Bash calls through the loop-cost gate.

usage: python3 replay.py <loop.tsv> <control.jsonl> [variants]
       python3 replay.py outcomes <calls.jsonl> [<start_utc of one call to probe>]
       python3 replay.py background-watch [days]

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


def background_watch(days):
    """Replay gate_policy.background_watch_decision at every recorded turn end
    (`turn_duration`) of the main-session transcripts modified in ``days`` days."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    import gate_policy
    import transcript
    cutoff = time.time() - days * 86400
    roots = [os.path.expanduser("~/.claude/projects")] + glob.glob(
        os.path.expanduser("~/.claude-accounts/*/projects"))
    paths = set()
    for root in roots:
        for path in glob.glob(os.path.join(root, "*", "*.jsonl")):
            if not os.path.basename(path).startswith("agent-") and os.path.getmtime(path) >= cutoff:
                paths.add(os.path.realpath(path))
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
            d = gate_policy.background_watch_decision(facts, False, False)
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


def main():
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
