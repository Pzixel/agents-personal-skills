"""Replay recorded Bash calls through the loop-cost gate.

usage: python3 replay.py <loop.tsv> <control.jsonl> [variants]

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
import json
import os
import subprocess
import sys

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


def main():
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
