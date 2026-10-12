"""Shared harness for the Dragonfly scripting lab.

Layout follows the repo architecture rule:
  * pure policy  -- percentiles(), format_table(), parse_script_latency(),
                    render_fragment(), merge_results() : plain functions over values.
  * thin I/O     -- connect(), load_script(), script_latency_text(), time_calls(),
                    write_raw_log(), emit() : small call sites, no business decisions.

Ports: primary 6379, replica 6380, single (proactor_threads=1, `single` profile) 6381.
"""
from __future__ import annotations

import os
import re
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path

import redis

LAB = Path(__file__).resolve().parent.parent
RESULTS_DIR = LAB / "results"
RESULTS_MD = LAB / "RESULTS.md"

PRIMARY_PORT = 6379
REPLICA_PORT = 6380
SINGLE_PORT = 6381

QUESTION_ORDER = ["q1", "q2", "q3", "q4", "q5", "q6", "q7", "q8", "q9",
                  "q10", "q11"]

# --------------------------------------------------------------------------
# I/O: connections and server facts
# --------------------------------------------------------------------------

def connect(port: int = PRIMARY_PORT, decode: bool = False) -> redis.Redis:
    return redis.Redis(host="127.0.0.1", port=port, decode_responses=decode,
                       socket_timeout=300)


def primary(decode: bool = False) -> redis.Redis:
    return connect(PRIMARY_PORT, decode)


def replica(decode: bool = False) -> redis.Redis:
    return connect(REPLICA_PORT, decode)


def single(decode: bool = False) -> redis.Redis:
    return connect(SINGLE_PORT, decode)


def server_version(r: redis.Redis) -> str:
    info = r.info("server")
    return f"{_s(info.get('dragonfly_version'))} (redis_version {_s(info.get('redis_version'))}), " \
           f"threads={_s(info.get('thread_count'))}"


def server_flags(r: redis.Redis) -> str:
    """Exact argv of the server process, read from docker (the flags under test)."""
    port = r.connection_pool.connection_kwargs["port"]
    name = {PRIMARY_PORT: "dfskill-primary", REPLICA_PORT: "dfskill-replica",
            SINGLE_PORT: "dfskill-single"}.get(port, "dfskill-primary")
    out = subprocess.run(["docker", "inspect", "-f", "{{json .Config.Cmd}}", name],
                         capture_output=True, text=True)
    return out.stdout.strip() or "(docker inspect failed)"


def _s(v) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)


# --------------------------------------------------------------------------
# I/O: scripts
# --------------------------------------------------------------------------

def unique_body(body: str) -> str:
    """Pure: append a run nonce as a Lua comment so the script gets a fresh sha.

    GOTCHA (v1.34.0): SCRIPT LATENCY histograms are cumulative per sha for the
    whole server lifetime and SCRIPT FLUSH does NOT reset them. A fresh sha per
    run is the only way to get a histogram that covers just this run.
    """
    return f"{body}\n-- run:{uuid.uuid4().hex}\n"


def load_script(r: redis.Redis, body: str, unique: bool = True) -> str:
    """SCRIPT LOAD -> sha (hex str). `unique` gives this run its own histogram."""
    if unique:
        body = unique_body(body)
    return _s(r.execute_command("SCRIPT", "LOAD", body))


def evalsha(r: redis.Redis, sha: str, keys: list, args: list | None = None):
    args = args or []
    return r.execute_command("EVALSHA", sha, len(keys), *keys, *args)


def info_text(r: redis.Redis, section: str = "ALL") -> str:
    """Raw INFO text (redis-py parses INFO into a dict by default; the section
    headers are part of what Q9 has to record verbatim)."""
    prev = r.response_callbacks.get("INFO")
    r.set_response_callback("INFO", lambda x, **kw: x)
    try:
        return _flatten(r.execute_command("INFO", section))
    finally:
        if prev is not None:
            r.set_response_callback("INFO", prev)


def script_flush(r: redis.Redis) -> None:
    """Drops the script cache; also resets the SCRIPT LATENCY histograms."""
    r.execute_command("SCRIPT", "FLUSH")


def script_latency_text(r: redis.Redis) -> str:
    """SCRIPT LATENCY replies with a nested array: one [sha, histogram-blob] pair
    per script that has been executed since the last SCRIPT FLUSH. Flatten it to
    the text redis-cli prints, which is what parse_script_latency() consumes."""
    return _flatten(r.execute_command("SCRIPT", "LATENCY"))


def _flatten(raw) -> str:
    if isinstance(raw, (list, tuple)):
        return "\n".join(_flatten(x) for x in raw)
    return _s(raw)


# --------------------------------------------------------------------------
# Pure: percentiles / tables
# --------------------------------------------------------------------------

def percentiles(samples_us: list[float]) -> dict:
    """Client-side stats over a list of microsecond samples. Nearest-rank percentiles."""
    if not samples_us:
        return {"n": 0}
    xs = sorted(samples_us)
    n = len(xs)

    def pct(p: float) -> float:
        idx = max(0, min(n - 1, int(round(p / 100.0 * n + 0.5)) - 1))
        return xs[idx]

    return {
        "n": n,
        "min": xs[0],
        "p50": pct(50),
        "p95": pct(95),
        "p99": pct(99),
        "max": xs[-1],
        "mean": statistics.fmean(xs),
        "total": sum(xs),
    }


def format_table(headers: list[str], rows: list[list]) -> str:
    cells = [[str(h) for h in headers]] + [[_fmt(c) for c in row] for row in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]
    out = ["| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(cells[0])) + " |",
           "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    for row in cells[1:]:
        out.append("| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(row)) + " |")
    return "\n".join(out)


def _fmt(c) -> str:
    if isinstance(c, float):
        return f"{c:.1f}"
    return str(c)


def stat_row(label: str, st: dict, extra: list | None = None) -> list:
    """One table row: label, n, p50, p95, p99, min, max, mean, total (all µs)."""
    if st.get("n", 0) == 0:
        return [label, 0, "-", "-", "-", "-", "-", "-", "-"] + (extra or [])
    return [label, st["n"], st["p50"], st["p95"], st["p99"], st["min"], st["max"],
            st["mean"], st["total"]] + (extra or [])


STAT_HEADERS = ["variant", "n", "p50_us", "p95_us", "p99_us", "min_us", "max_us",
                "mean_us", "total_us"]


# --------------------------------------------------------------------------
# Pure: SCRIPT LATENCY parsing
# --------------------------------------------------------------------------
# Verbatim v1.34.0 format (one block per called sha, blocks separated by a blank
# line; the whole reply is a single bulk string):
#
#   5d79f52e2ca583cf4f7a3a9ce06f56432aba1498
#   Count: 2 Average: 162.0000  StdDev: 40.00
#   Min: 122.0000  Median: 130.0000  Max: 202.0000
#   ------------------------------------------------------
#   [     120,     140 ) 1  50.000%  50.000% ##########
#   [     200,     250 ) 1  50.000% 100.000% ##########
#
# Units are microseconds (SCRIPT HELP: "Prints latency histograms in usec").

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SUMMARY_RE = re.compile(
    r"Count:\s*(\d+)\s+Average:\s*([\d.]+)\s+StdDev:\s*([\d.]+)")
_MINMED_RE = re.compile(
    r"Min:\s*([\d.]+)\s+Median:\s*([\d.]+)\s+Max:\s*([\d.]+)")
_BUCKET_RE = re.compile(
    r"^\[\s*(\d+),\s*(\d+)\s*\)\s+(\d+)\s+([\d.]+)%\s+([\d.]+)%")


def parse_script_latency(text: str, sha: str | None = None) -> dict:
    """Parse SCRIPT LATENCY output. Returns {sha: {count, average_us, stddev,
    min_us, median_us, max_us, buckets:[(lo,hi,count,pct,cum_pct)], raw}}.
    If `sha` is given, returns that single entry ({} when the sha is absent)."""
    blocks: dict[str, dict] = {}
    cur: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if _SHA_RE.match(stripped):
            cur = stripped
            blocks[cur] = {"sha": cur, "buckets": [], "raw": []}
            continue
        if cur is None:
            continue
        blocks[cur]["raw"].append(line)
        if m := _SUMMARY_RE.search(line):
            blocks[cur].update(count=int(m.group(1)), average_us=float(m.group(2)),
                               stddev=float(m.group(3)))
        elif m := _MINMED_RE.search(line):
            blocks[cur].update(min_us=float(m.group(1)), median_us=float(m.group(2)),
                               max_us=float(m.group(3)))
        elif m := _BUCKET_RE.match(stripped):
            blocks[cur]["buckets"].append(
                (int(m.group(1)), int(m.group(2)), int(m.group(3)),
                 float(m.group(4)), float(m.group(5))))
    for b in blocks.values():
        b["raw"] = "\n".join(b["raw"]).strip()
    if sha is None:
        return blocks
    return blocks.get(sha.lower(), {})


def format_script_latency(entry: dict) -> str:
    """One-line summary of a parsed SCRIPT LATENCY block, or a why-not note."""
    if not entry:
        return "SCRIPT LATENCY: no histogram for this sha"
    return (f"SCRIPT LATENCY n={entry.get('count')} avg={entry.get('average_us')}us "
            f"median={entry.get('median_us')}us max={entry.get('max_us')}us "
            f"buckets={len(entry.get('buckets', []))}")


# --------------------------------------------------------------------------
# I/O: timing
# --------------------------------------------------------------------------

def time_calls(fn, iters: int = 200, warmup: int = 20) -> list[float]:
    """Call fn() warmup+iters times; return the measured samples in MICROSECONDS.
    No smoothing, no outlier removal."""
    for _ in range(warmup):
        fn()
    out = []
    for _ in range(iters):
        t0 = time.perf_counter_ns()
        fn()
        out.append((time.perf_counter_ns() - t0) / 1000.0)
    return out


def payload(nbytes: int, seed: str = "x") -> bytes:
    return (seed * ((nbytes // len(seed)) + 1)).encode()[:nbytes]


# --------------------------------------------------------------------------
# I/O: raw logs and RESULTS.md
# --------------------------------------------------------------------------

class RawLog:
    """Per-run raw log at lab/results/<question>.txt. Records server version,
    server flags and every command line the benchmark used."""

    def __init__(self, question: str, r: redis.Redis | None = None, note: str = ""):
        self.question = question
        self.lines: list[str] = []
        self.path = RESULTS_DIR / f"{question}.txt"
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        self.w(f"=== {question.upper()} raw log ===")
        self.w(f"date: {time.strftime('%Y-%m-%dT%H:%M:%S%z')}")
        self.w(f"driver: {Path(sys.argv[0]).name}")
        if note:
            self.w(f"note: {note}")
        if r is not None:
            self.w(f"server version: {server_version(r)}")
            self.w(f"server flags:   {server_flags(r)}")
        self.w("")

    def w(self, s: str = "") -> None:
        self.lines.append(s)
        print(s)

    def cmd(self, s: str) -> None:
        """Record an exact command line that was issued."""
        self.w(f"$ {s}")

    def section(self, title: str) -> None:
        self.w("")
        self.w(f"--- {title} ---")

    def save(self) -> None:
        self.path.write_text("\n".join(self.lines) + "\n")


def render_fragment(question: str, title: str, summary_row: list[str],
                    body: str) -> str:
    """Pure: the RESULTS.md fragment for one question."""
    row = " | ".join(str(c) for c in summary_row)
    return (f"<!--BEGIN {question}-->\n"
            f"<!--SUMMARY| {row} |-->\n"
            f"### {question.upper()} — {title}\n\n"
            f"{body.rstrip()}\n\n"
            f"Raw log: [`results/{question}.txt`](results/{question}.txt)\n"
            f"<!--END {question}-->")


# NOTE: `q\d+`, not `q\d` -- with a single-digit class, emitting any q1-q9
# fragment silently DROPPED the q10+ blocks (and their summary rows) because
# merge_results only carries over fragments this regex finds.
_FRAG_RE = re.compile(r"<!--BEGIN (q\d+)-->.*?<!--END \1-->", re.S)
_SUMMARY_LINE_RE = re.compile(r"^<!--SUMMARY\|(.*)\|-->$", re.M)

SUMMARY_HEADERS = ["Q", "experiment", "key numbers", "conclusion"]

PENDING = {q: f"| {q.upper()} | pending (part B) | - | - |" for q in
           ("q5", "q6", "q7", "q8")}


def merge_results(existing: str, fragment: str, question: str) -> str:
    """Pure: replace the fragment for `question` in `existing` (or append it),
    then rebuild the leading summary table from all fragments present."""
    frags: dict[str, str] = {m.group(0)[len(f"<!--BEGIN "):].split("-->")[0]: m.group(0)
                             for m in _FRAG_RE.finditer(existing)}
    frags[question] = fragment
    ordered = [frags[q] for q in QUESTION_ORDER if q in frags]

    rows = []
    for q in QUESTION_ORDER:
        if q in frags:
            m = _SUMMARY_LINE_RE.search(frags[q])
            rows.append("|" + (m.group(1) + "|" if m else f" {q.upper()} | - | - | - |"))
        elif q in PENDING:
            rows.append(PENDING[q])

    header = [
        "# Dragonfly v1.34.0 scripting lab — results",
        "",
        "Lab: `lab/compose.yaml` (primary :6379, replica :6380, `single` profile :6381),",
        "image digest `sha256:366e34f4…95db`, args",
        "`--cache_mode=false --maxmemory=2048Mi --dbfilename=dump --proactor_threads=4`.",
        "Timings are client-side wall clock in microseconds, reported as-is",
        "(no smoothing, no outlier removal). Regenerate with `lab/run_all.sh`.",
        "",
        "## Summary",
        "",
        "| Q | experiment | key numbers | conclusion |",
        "|---|---|---|---|",
        *rows,
        "",
        "## Appendix — per-question detail",
        "",
    ]
    body = "\n\n".join(ordered)
    return "\n".join(header) + body + "\n"


def emit(question: str, title: str, summary_row: list[str], body: str) -> None:
    """I/O: merge this question's fragment into lab/RESULTS.md."""
    frag = render_fragment(question, title, summary_row, body)
    existing = RESULTS_MD.read_text() if RESULTS_MD.exists() else ""
    RESULTS_MD.write_text(merge_results(existing, frag, question))
    print(f"\n[{question}] RESULTS.md updated ({RESULTS_MD})")


def restart(container: str = "dfskill-primary", port: int = PRIMARY_PORT,
            timeout_s: float = 60.0) -> None:
    """Restart a lab container and wait for PING.

    GOTCHA: Dragonfly's OOM check is against RSS, and RSS stays high after a
    large Lua allocation (a 32 MiB cjson.encode leaves ~2.5 GiB RSS against
    --maxmemory=2048Mi, after which every write returns -ERR Out of memory).
    FLUSHALL does not bring RSS back; only a restart does.
    """
    subprocess.run(["docker", "restart", container], capture_output=True, check=True)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if connect(port).ping():
                return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"{container} did not come back within {timeout_s}s")


# --------------------------------------------------------------------------
# I/O: throwaway node with non-default server flags
# --------------------------------------------------------------------------
# Flags that cannot be changed at runtime (--lock_on_hashtags, --lua_auto_async)
# are measured on a disposable container so the lab keeps its default args.
# Lifted out of q5 in part C so q10 reuses exactly the same mechanism.

FLAG_PORT = 6382
FLAG_NAME = "dfskill-flag"
BASE_ARGS = ["--cache_mode=false", "--maxmemory=2048Mi", "--dbfilename=dump",
             "--proactor_threads=4"]
IMAGE = ("docker.dragonflydb.io/dragonflydb/dragonfly@sha256:"
         "366e34f415c22983dc1c4a1d575fa2d9c22b9a3998a9934bf1cc6d16c17695db")


def start_flag_node(extra: list[str], name: str = FLAG_NAME, port: int = FLAG_PORT,
                    timeout_s: float = 60.0) -> redis.Redis:
    """I/O: (re)create the throwaway node with BASE_ARGS + `extra`; return a client."""
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", name, "--memory", "3g",
                    "--ulimit", "memlock=-1", "-p", f"{port}:6379", IMAGE,
                    *BASE_ARGS, *extra], capture_output=True, check=True)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = connect(port)
            if r.ping():
                return r
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"{name} did not start")


def stop_flag_node(name: str = FLAG_NAME) -> None:
    """I/O: remove the throwaway node -- the lab is back to its default flags."""
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def flushall(*clients: redis.Redis) -> None:
    for c in clients:
        c.flushall()


# --------------------------------------------------------------------------
# Script/transaction execution-path counters (INFO ALL)
# --------------------------------------------------------------------------
# Added in part B. Which execution path a script took is observable in INFO:
#   eval_shardlocal_coordination_total -- scripts run on the shard-local fast path
#   eval_io_coordination_total         -- scripts that needed cross-shard hops
#   eval_squashed_flushes              -- flushes of the squashed-command pipeline
#   lua_blocked_total / blocked_on_interpreter -- waits for a free interpreter
#   tx_*                               -- transaction scheduling counters
# `tx_with_freq` / `squash_with_freq` are comma-separated vectors, not scalars,
# and are carried through as strings by counters_text() only.
#
# Usage:  before = script_counters(r) ; ... ; delta = counter_delta(before,
#         script_counters(r)) ; log.w(format_counters(delta))
# A follow-up agent can retrofit this onto Q1-Q4 unchanged.

_COUNTER_PREFIXES = ("eval_", "lua_", "tx_", "squash", "multi_")
_COUNTER_EXTRA = ("blocked_on_interpreter", "commands_squashing_replies_bytes",
                  "used_memory_lua")


def _is_counter(name: str) -> bool:
    return name.startswith(_COUNTER_PREFIXES) or name in _COUNTER_EXTRA


def parse_counters(info: str) -> dict:
    """Pure: numeric script/tx counters out of raw INFO text."""
    out = {}
    for line in info.splitlines():
        if ":" not in line or line.startswith("#"):
            continue
        name, _, value = line.strip().partition(":")
        if not _is_counter(name):
            continue
        try:
            out[name] = float(value)
        except ValueError:
            continue
    return out


def script_counters(r: redis.Redis) -> dict:
    """I/O: snapshot of the script/tx execution-path counters."""
    return parse_counters(info_text(r, "ALL"))


def counter_delta(before: dict, after: dict, include_zero: bool = False) -> dict:
    """Pure: after-before for every counter, dropping unchanged ones by default.
    Gauges (used_memory_lua, lua_interpreter_cnt) diff too -- read them as such."""
    keys = sorted(set(before) | set(after))
    out = {k: after.get(k, 0.0) - before.get(k, 0.0) for k in keys}
    return out if include_zero else {k: v for k, v in out.items() if v}


def format_counters(delta: dict, keys: tuple = (
        "eval_shardlocal_coordination_total", "eval_io_coordination_total",
        "eval_squashed_flushes", "lua_blocked_total")) -> str:
    """Pure: one line with the headline path counters plus everything else that moved."""
    head = " ".join(f"{k.replace('_total', '')}={delta.get(k, 0):+.0f}" for k in keys)
    rest = " ".join(f"{k}={v:+.0f}" for k, v in sorted(delta.items())
                    if k not in keys)
    return f"{head}" + (f" | {rest}" if rest else "")
