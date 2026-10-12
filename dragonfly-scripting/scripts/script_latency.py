#!/usr/bin/env python3
"""Per-sha server-side script latency from Dragonfly's SCRIPT LATENCY histograms.

The histograms are CUMULATIVE since the script was first loaded; nothing short
of restarting the node resets them (SCRIPT FLUSH does not), and `SCRIPT STATS`
does not exist (SCRIPT FLAGS is a setter only). So the default table answers
"since server start"; --watch prints
the delta since the previous sample, which is what you want while a load test
runs.

p50/p95/p99 are read off the histogram's cumulative-percent column, so each one
is the upper bound of the bucket that crosses that percentile: exact to bucket
resolution, never better. `median` is the server's own reported median.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (bucket_percentiles, connect, format_table,  # noqa: E402
                     parse_script_latency, script_latency_text, server_version,
                     subtract_entry)

HEADERS = ["sha", "n", "p50_us", "p95_us", "p99_us", "median_us", "max_us", "avg_us"]


def rows(entries: dict, sha_prefix: str | None) -> list[list]:
    """Pure: one table row per sha, longest-running first."""
    out = []
    for sha, entry in entries.items():
        if sha_prefix and not sha.startswith(sha_prefix.lower()):
            continue
        p = bucket_percentiles(entry)
        out.append([sha[:12], p["n"], p["p50"], p["p95"], p["p99"],
                    p["median_us"], p["max_us"], p["avg_us"]])
    return sorted(out, key=lambda r: -(r[1] or 0))


def deltas(now: dict, before: dict) -> dict:
    """Pure: per-sha histogram delta, dropping shas with no new samples."""
    out = {}
    for sha, entry in now.items():
        d = subtract_entry(entry, before.get(sha, {}))
        if d.get("count"):
            out[sha] = d
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=6379)
    p.add_argument("--sha", help="only shas starting with this prefix")
    p.add_argument("--watch", nargs="?", type=float, const=2.0, metavar="SECONDS",
                   help="re-print every SECONDS (default 2) as a delta since the "
                        "previous sample; Ctrl-C to stop")
    p.add_argument("--raw", action="store_true",
                   help="dump the server's histogram text for the selected shas")
    a = p.parse_args(argv)

    r = connect(a.host, a.port)
    entries = parse_script_latency(script_latency_text(r))
    if a.raw:
        for sha, e in entries.items():
            if not a.sha or sha.startswith(a.sha.lower()):
                print(f"{sha}\n{e['raw']}\n")
        return 0
    if not a.watch:
        print(f"{a.host}:{a.port} {server_version(r)} -- cumulative since server start")
        print(format_table(HEADERS, rows(entries, a.sha)) if entries
              else "no script has run on this node yet")
        return 0

    previous = entries
    try:
        while True:
            time.sleep(a.watch)
            entries = parse_script_latency(script_latency_text(r))
            window = deltas(entries, previous)
            previous = entries
            print(f"\n== {time.strftime('%H:%M:%S')} last {a.watch:g}s ==")
            print(format_table(HEADERS, rows(window, a.sha)) if window
                  else "(no script invocations in this window)")
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
