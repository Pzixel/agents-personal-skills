"""Replay recorded Bash calls through the loop-cost gate.

usage: python3 replay.py <loop.tsv> <control.jsonl> [variants]
       python3 replay.py outcomes <calls.jsonl> [<start_utc of one call to probe>]

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
import re
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


_INTERPRETERS = re.compile(r"^(python[0-9.]*|bash|sh|zsh|node|ruby|perl|Rscript)$")
_SCRIPT_SUFFIX = re.compile(r"\.(py|sh|js|mjs|ts|rb|pl)$")
_SYSTEM_DIRS = ("/usr/", "/bin/", "/sbin/", "/opt/homebrew/")
_SERVICE_PROGRAMS = frozenset(
    {"vite", "caddy", "nginx", "uvicorn", "gunicorn", "serve", "http-server", "redis-server"}
)


def _launch_kind(tokens):
    """``script`` when the launched command runs a file (or inline code) the
    session wrote, ``service`` for a long-lived server or tunnel, else ``other``."""
    i = 0
    while i < len(tokens) and (g._ENV_ASSIGN.match(tokens[i]) or tokens[i] in g._HEAD_PREFIXES
                               or tokens[i] in g._DETACH_HEADS or tokens[i] in ("env", "rtk")):
        i += 1
    tokens = tokens[i:]
    prog = g._program(tokens)
    args = tokens[1:]
    if not tokens:
        return "other"
    if prog in _SERVICE_PROGRAMS or prog == "tail" and any(a in ("-f", "-F") for a in args):
        return "service"
    if prog in ("npm", "pnpm", "yarn", "bun") and any(a in ("dev", "start", "serve", "preview") for a in args):
        return "service"
    if prog == "next" and args[:1] == ["dev"] or prog in ("cargo", "go") and args[:1] == ["run"]:
        return "service"
    if prog == "kubectl" and "port-forward" in args or prog == "ssh" and any(
            a.startswith("-") and ("L" in a or "N" in a or "R" in a) for a in args):
        return "service"
    if prog in ("docker", "docker-compose", "podman") and "up" in args:
        return "service"
    if _INTERPRETERS.match(prog):
        rest = [a for a in args if not a.startswith("-") or a == "-"]
        if "-m" in args or "-c" in args or not rest:
            return "other"
        target = rest[0]
        return "script" if target == "-" or "/" in target or _SCRIPT_SUFFIX.search(target) else "other"
    first = tokens[0]
    if prog in ("wait-for", "check-run") or first.startswith(_SYSTEM_DIRS):
        return "other"
    return "script" if "/" in first or _SCRIPT_SUFFIX.search(first) else "other"


def _launched(script, background):
    """Token lists of the commands that outlive the call."""
    if background:
        return [g._segment_head(seg) for seg in g._segments(script)]
    out = []
    pos = 0
    for match in g._BACKGROUND_AMP.finditer(script):
        before = g._segments(script[pos:match.start()])
        if before:
            out.append(before[-1].split())
        pos = match.end()
    for seg in g._segments(script):
        tokens = seg.split()
        heads = [t for t in tokens[:4] if t in g._DETACH_HEADS or t in ("tmux", "screen")]
        if heads:
            out.append(tokens)
    return out


def launch_class(command, background):
    """``script``/``service``/``other`` for the backtest split (script wins)."""
    script, _ = g._split_heredocs(command or "")
    script = g._strip_quotes("\n".join(l for l in script.split("\n") if not l.lstrip().startswith("#")))
    kinds = {_launch_kind(t) for t in _launched(script, background)}
    for kind in ("script", "service"):
        if kind in kinds:
            return kind
    return "other"


def outcomes(calls_path, probe_ts=None):
    """Outcomes gate over every call of extract.py: fires per trigger class and
    per day, the 10 most recent fires, and the decision on the call at probe_ts."""
    rows = sorted((json.loads(l) for l in open(calls_path)), key=lambda r: r["start_utc"])
    fires = []
    for r in rows:
        d = g.outcomes_decision(r["command"], r.get("run_in_background"))
        if d["decision"] == "deny":
            fires.append(dict(r, trigger=d["trigger"], kind=launch_class(
                r["command"], d["trigger"] == "background")))
        if probe_ts and r["start_utc"] == probe_ts:
            print("probe", probe_ts, d["decision"], d["trigger"])
    print("calls", len(rows), "fires", len(fires),
          "by trigger", dict(collections.Counter(f["trigger"] for f in fires)),
          "by agent", dict(collections.Counter("sub" if f["agent_type"] else "main" for f in fires)))
    print("by launch class and trigger", sorted(collections.Counter(
        (f["kind"], f["trigger"]) for f in fires).items()))
    script = [f for f in fires if f["kind"] == "script"]
    days = collections.Counter(f["start_utc"][:10] for f in fires)
    script_days = collections.Counter(f["start_utc"][:10] for f in script)
    print("day | all fires | class (a) script fires")
    for day in sorted(days):
        print(day, days[day], script_days[day])
    print("10 most recent class (a) fires")
    for f in script[-10:]:
        print(f["start_utc"], f["trigger"], f["command"][:120].replace("\n", " "))


def main():
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
