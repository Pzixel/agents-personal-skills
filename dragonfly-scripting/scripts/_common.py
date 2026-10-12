"""Shared bits for the redis-touching scripts. Only third-party dep: `redis`.

Vendored from the lab harness so the skill folder works wherever it is copied.
"""
from __future__ import annotations

import re
import statistics
import time

try:
    import redis
except ImportError:  # deferred so --help works without the client installed
    redis = None


# ---------------------------------------------------------------------------
# I/O: connection, scripts, INFO
# ---------------------------------------------------------------------------

def connect(host: str = "127.0.0.1", port: int = 6379, decode: bool = False):
    if redis is None:
        raise SystemExit("this tool needs the redis client: pip install redis")
    return redis.Redis(host=host, port=port, decode_responses=decode,
                       socket_timeout=300)


def _s(v) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)


def flatten(raw) -> str:
    if isinstance(raw, (list, tuple)):
        return "\n".join(flatten(x) for x in raw)
    return _s(raw)


def load_script(r, body: str) -> str:
    return _s(r.execute_command("SCRIPT", "LOAD", body))


def evalsha(r, sha: str, keys: list, args: list | None = None):
    args = args or []
    return r.execute_command("EVALSHA", sha, len(keys), *keys, *args)


def info_text(r, section: str = "ALL") -> str:
    """Raw INFO text; redis-py parses INFO into a dict by default."""
    prev = r.response_callbacks.get("INFO")
    r.set_response_callback("INFO", lambda x, **kw: x)
    try:
        return flatten(r.execute_command("INFO", section))
    finally:
        if prev is not None:
            r.set_response_callback("INFO", prev)


def script_latency_text(r) -> str:
    """SCRIPT LATENCY replies with [sha, histogram-blob] pairs; flatten to the
    text redis-cli prints, which is what parse_script_latency() consumes."""
    return flatten(r.execute_command("SCRIPT", "LATENCY"))


def server_version(r) -> str:
    info = r.info("server")
    return (f"{_s(info.get('dragonfly_version'))} "
            f"(redis_version {_s(info.get('redis_version'))}), "
            f"threads={_s(info.get('thread_count'))}")


def time_calls(fn, iters: int = 200, warmup: int = 20, setup=None) -> list[float]:
    """Call fn() warmup+iters times; return the measured samples in MICROSECONDS.
    `setup` runs before every call and is NOT timed (state reset for scripts that
    are not idempotent). No smoothing, no outlier removal."""
    for _ in range(warmup):
        if setup:
            setup()
        fn()
    out = []
    for _ in range(iters):
        if setup:
            setup()
        t0 = time.perf_counter_ns()
        fn()
        out.append((time.perf_counter_ns() - t0) / 1000.0)
    return out


# ---------------------------------------------------------------------------
# Pure: SCRIPT LATENCY parsing
# ---------------------------------------------------------------------------

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SUMMARY_RE = re.compile(r"Count:\s*(\d+)\s+Average:\s*([\d.]+)\s+StdDev:\s*([\d.]+)")
_MINMED_RE = re.compile(r"Min:\s*([\d.]+)\s+Median:\s*([\d.]+)\s+Max:\s*([\d.]+)")
_BUCKET_RE = re.compile(r"^\[\s*(\d+),\s*(\d+)\s*\)\s+(\d+)\s+([\d.]+)%\s+([\d.]+)%")


def parse_script_latency(text: str, sha: str | None = None) -> dict:
    """Parse SCRIPT LATENCY output -> {sha: {count, average_us, stddev, min_us,
    median_us, max_us, buckets:[(lo,hi,count,pct,cum_pct)], raw}}.
    With `sha`, returns that single entry ({} when the sha is absent)."""
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
    if not entry:
        return "SCRIPT LATENCY: no histogram for this sha"
    med = entry.get("median_us")
    # a delta histogram has no server-reported median; fall back to the bucket
    # that crosses 50%, which is an upper bound, not the median itself
    med = f"median={med}us" if med is not None \
        else f"p50<={bucket_percentiles(entry)['p50']}us"
    return (f"SCRIPT LATENCY n={entry.get('count')} avg={entry.get('average_us')}us "
            f"{med} max={entry.get('max_us')}us "
            f"buckets={len(entry.get('buckets', []))}")


def bucket_percentiles(entry: dict) -> dict:
    """Pure: p50/p95/p99 read off the cumulative-percent column of the server
    histogram. Bucket-resolution only: the value reported is the bucket's upper
    bound, so it overstates by up to one bucket width."""
    out = {"n": entry.get("count", 0), "median_us": entry.get("median_us"),
           "max_us": entry.get("max_us"), "avg_us": entry.get("average_us")}
    for want, name in ((50.0, "p50"), (95.0, "p95"), (99.0, "p99")):
        out[name] = None
        for _lo, hi, _c, _pct, cum in entry.get("buckets", []):
            if cum >= want:
                out[name] = hi
                break
    return out


def subtract_entry(now: dict, before: dict) -> dict:
    """Pure: histogram delta. SCRIPT LATENCY never resets, so a window view has
    to subtract a snapshot bucket by bucket; percentiles are then recomputed
    from the delta counts."""
    if not before:
        return now
    lo_counts = {(b[0], b[1]): b[2] for b in before.get("buckets", [])}
    buckets = []
    for lo, hi, count, _pct, _cum in now.get("buckets", []):
        d = count - lo_counts.get((lo, hi), 0)
        if d > 0:
            buckets.append((lo, hi, d))
    total = sum(b[2] for b in buckets)
    out = {"sha": now.get("sha"), "count": total, "buckets": [], "raw": ""}
    run = 0
    for lo, hi, d in buckets:
        run += d
        out["buckets"].append((lo, hi, d, 100.0 * d / total if total else 0.0,
                               100.0 * run / total if total else 0.0))
    n_before, n_now = before.get("count", 0), now.get("count", 0)
    a_before, a_now = before.get("average_us", 0.0), now.get("average_us", 0.0)
    if total:
        out["average_us"] = round((a_now * n_now - a_before * n_before) / total, 3)
    out["median_us"] = None
    out["max_us"] = buckets[-1][1] if buckets else None
    return out


# ---------------------------------------------------------------------------
# Pure: client-side percentiles and tables
# ---------------------------------------------------------------------------

def percentiles(samples_us: list[float]) -> dict:
    """Nearest-rank percentiles over microsecond samples."""
    if not samples_us:
        return {"n": 0}
    xs = sorted(samples_us)
    n = len(xs)

    def pct(p: float) -> float:
        idx = max(0, min(n - 1, int(round(p / 100.0 * n + 0.5)) - 1))
        return xs[idx]

    return {"n": n, "min": xs[0], "p50": pct(50), "p95": pct(95), "p99": pct(99),
            "max": xs[-1], "mean": statistics.fmean(xs), "total": sum(xs)}


def format_table(headers: list[str], rows: list[list]) -> str:
    cells = [[str(h) for h in headers]] + [[_fmt(c) for c in row] for row in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]
    out = ["| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(cells[0])) + " |",
           "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    for row in cells[1:]:
        out.append("| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(row)) + " |")
    return "\n".join(out)


def _fmt(c) -> str:
    return f"{c:.1f}" if isinstance(c, float) else str(c)


# ---------------------------------------------------------------------------
# Pure: execution-path counters
# ---------------------------------------------------------------------------
# eval_shardlocal_coordination_total -- script ran on the shard-local fast path
# eval_io_coordination_total         -- script needed cross-shard hops
# The split is the regime detector: an all-on-one-shard script that shows up in
# eval_io_coordination_total is paying coordination it does not need.

COUNTERS = ("eval_shardlocal_coordination_total", "eval_io_coordination_total",
            "eval_squashed_flushes", "lua_blocked_total", "blocked_on_interpreter")


def parse_counters(info: str, names=COUNTERS) -> dict:
    """Pure: the named numeric counters out of raw INFO text."""
    out = {}
    for line in info.splitlines():
        if ":" not in line or line.startswith("#"):
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if k in names:
            try:
                out[k] = float(v) if "." in v else int(v)
            except ValueError:
                pass
    return out


def counter_delta(before: dict, after: dict) -> dict:
    return {k: after.get(k, 0) - v for k, v in before.items()}


def script_counters(r) -> dict:
    return parse_counters(info_text(r, "ALL"))
