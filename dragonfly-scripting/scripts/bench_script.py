#!/usr/bin/env python3
"""Benchmark one Lua script, or two variants against the same seeded state.

  bench_script.py --spec spec.json --seed seed.py script.lua
  bench_script.py --spec spec.json --seed seed.py --compare a.lua b.lua

SPEC (JSON) -- the invocation, nothing else:
  {
    "keys":  ["k1", "k2"],          # KEYS, in order; EVALSHA numkeys = len(keys)
    "argv":  ["a1", "a2"],          # ARGV, in order
    "iters": 200,                   # timed calls   (default 200)
    "warmup": 20,                   # untimed calls (default 20)
    "ignore_reply_indices": [1]     # flat reply positions carrying the server
  }                                 # clock; excluded from the --compare check

SEED -- your script, run as `<seed> <host> <port>` (a .py seed runs under the
current interpreter). It must leave the server in the state the benchmark
assumes. If it prints a JSON object on stdout, that object is merged into the
spec, so the seed can supply per-run KEYS/ARGV (fresh tokens, a run id read
from INFO, ...). With --reseed it runs before every call and is not timed,
which is how a non-idempotent script gets measured on its real path instead of
on its second-call path.

Reported: client-side p50/p95/p99 (wall time of EVALSHA, so network + queueing
+ execution), the server's own SCRIPT LATENCY entry for that sha, and the
eval_shardlocal_coordination_total / eval_io_coordination_total delta per
invocation -- the split that says whether the script stayed on one shard.

--compare asserts the two replies are byte-identical (outside
ignore_reply_indices) BEFORE reporting: two scripts that answer differently are
not two versions of the same script, and the exit code is 2.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (connect, counter_delta, evalsha, format_script_latency,  # noqa: E402
                     format_table, load_script, parse_script_latency,
                     percentiles, script_counters, script_latency_text,
                     server_version, subtract_entry, time_calls)

HEADERS = ["script", "n", "p50_us", "p95_us", "p99_us", "min_us", "max_us", "mean_us"]


# ---------------------------------------------------------------------------
# Pure
# ---------------------------------------------------------------------------

def flat(reply) -> list:
    out = []
    for x in reply if isinstance(reply, (list, tuple)) else [reply]:
        out.extend(flat(x)) if isinstance(x, (list, tuple)) else out.append(x)
    return out


def reply_diffs(a, b, ignore: set[int]) -> list[tuple[int, object, object]]:
    """Pure: positions where two flattened replies disagree, skipping the
    positions the spec declares clock-derived."""
    fa, fb = flat(a), flat(b)
    if len(fa) != len(fb):
        return [(-1, f"len={len(fa)}", f"len={len(fb)}")]
    return [(i, x, y) for i, (x, y) in enumerate(zip(fa, fb))
            if i not in ignore and x != y]


def stat_row(label: str, st: dict) -> list:
    if not st.get("n"):
        return [label, 0] + ["-"] * 6
    return [label, st["n"], st["p50"], st["p95"], st["p99"], st["min"], st["max"],
            st["mean"]]


def coordination_line(delta: dict, calls: int) -> str:
    shard = delta.get("eval_shardlocal_coordination_total", 0)
    io = delta.get("eval_io_coordination_total", 0)
    total = shard + io
    regime = ("shard-local" if io == 0 else
              "cross-shard" if shard == 0 else "mixed")
    return (f"coordination per invocation: shardlocal={shard / calls:.2f} "
            f"io={io / calls:.2f} (of {total / calls:.2f} total) -> {regime}")


def merge_spec(base: dict, extra: dict) -> dict:
    out = dict(base)
    out.update(extra)
    return out


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

def run_seed(seed: Path | None, host: str, port: int) -> dict:
    """Run the user's seed program; its stdout JSON (if any) extends the spec."""
    if seed is None:
        return {}
    cmd = ([sys.executable, str(seed)] if seed.suffix == ".py" else [str(seed)])
    out = subprocess.run(cmd + [host, str(port)], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"seed {seed} failed (rc={out.returncode}):\n{out.stderr}")
    text = out.stdout.strip()
    return json.loads(text) if text.startswith("{") else {}


def bench_one(r, path: Path, spec: dict, seed: Path | None, host: str, port: int,
              reseed: bool) -> tuple[dict, dict, object, dict]:
    """Load, call once for the reply, then time it. Returns
    (client stats, latency entry, first reply, counter delta)."""
    sha = load_script(r, path.read_text())
    state = {"spec": spec}

    def setup():
        if reseed:
            state["spec"] = merge_spec(spec, run_seed(seed, host, port))

    def call():
        s = state["spec"]
        return evalsha(r, sha, s["keys"], s["argv"])

    setup()
    reply = call()
    iters, warmup = spec.get("iters", 200), spec.get("warmup", 20)
    before = script_counters(r)
    before_hist = parse_script_latency(script_latency_text(r), sha)
    samples = time_calls(call, iters=iters, warmup=warmup,
                         setup=setup if reseed else None)
    delta = counter_delta(before, script_counters(r))
    # SCRIPT LATENCY never resets, so subtract the pre-run histogram: otherwise
    # a sha that ran yesterday reports yesterday's tail.
    entry = subtract_entry(parse_script_latency(script_latency_text(r), sha),
                           before_hist)
    # the counter delta covers warmup+timed calls, not just the timed ones
    return percentiles(samples), entry, reply, (delta, iters + warmup)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("script", nargs="?", type=Path, help="the .lua file to run")
    p.add_argument("--compare", nargs=2, type=Path, metavar=("A.lua", "B.lua"),
                   help="run both on the same seed and require identical replies")
    p.add_argument("--spec", type=Path, required=True, help="JSON invocation spec")
    p.add_argument("--seed", type=Path, help="program that seeds the server")
    p.add_argument("--reseed", action="store_true",
                   help="re-run the seed before every call (not timed)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=6379)
    a = p.parse_args(argv)

    targets = list(a.compare) if a.compare else ([a.script] if a.script else [])
    if not targets:
        p.error("give a script, or --compare A.lua B.lua")

    r = connect(a.host, a.port)
    base = json.loads(a.spec.read_text())
    spec = merge_spec(base, run_seed(a.seed, a.host, a.port))
    ignore = set(spec.get("ignore_reply_indices", []))
    print(f"{a.host}:{a.port} {server_version(r)}")
    print(f"spec: {len(spec['keys'])} keys, {len(spec['argv'])} argv, "
          f"iters={spec.get('iters', 200)} warmup={spec.get('warmup', 20)}"
          f"{' reseed-per-call' if a.reseed else ''}")

    results, rows = [], []
    for path in targets:
        st, entry, reply, (delta, ncalls) = bench_one(r, path, spec, a.seed, a.host, a.port,
                                            a.reseed)
        results.append((path, st, entry, reply, delta, ncalls))
        rows.append(stat_row(path.name, st))

    if a.compare:
        diffs = reply_diffs(results[0][3], results[1][3], ignore)
        if diffs:
            print(f"\nREPLIES DIFFER at {len(diffs)} position(s) "
                  f"(ignored {sorted(ignore)}):")
            for i, x, y in diffs[:10]:
                print(f"  [{i}] {results[0][0].name}={x!r}  {results[1][0].name}={y!r}")
            print("the variants do not implement the same contract on this seed; "
                  "timing them against each other would compare two behaviours")
            return 2
        print(f"\nreplies identical on this seed "
              f"({len(flat(results[0][3]))} fields, ignored {sorted(ignore)})")

    print()
    print(format_table(HEADERS, rows))
    for path, st, entry, _reply, delta, ncalls in results:
        calls = max(ncalls, 1)
        print(f"\n{path.name}: {format_script_latency(entry)}")
        print(f"{path.name}: {coordination_line(delta, calls)}")
    if a.compare:
        p50a, p50b = results[0][1]["p50"], results[1][1]["p50"]
        print(f"\np50 ratio {results[1][0].name} / {results[0][0].name} = "
              f"{p50b / p50a:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
