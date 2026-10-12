#!/usr/bin/env python
"""Q2 — write batching: 32 items x (5 HSET + 1 ZADD) = 192 redis.call, versus
5 multi-field HSET + 1 multi-member ZADD = 6 redis.call. Same 192 field writes
and 32 zset members either way."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common

QUESTION = "q2"
ITEMS = 32
FIELDS_PER_ITEM = 5
ITERS = 200
WARMUP = 20
VALUE_BYTES = 64
KEYS = [f"{{q2}}h{i}" for i in range(FIELDS_PER_ITEM)] + ["{q2}z"]

PER_CALL = """
local n = tonumber(ARGV[1])
local v = ARGV[2]
local z = KEYS[#KEYS]
for i = 1, n do
  for f = 1, #KEYS - 1 do
    redis.call('HSET', KEYS[f], 'item' .. i, v)
  end
  redis.call('ZADD', z, i, 'item' .. i)
end
return n
"""

BATCHED = """
local n = tonumber(ARGV[1])
local v = ARGV[2]
local z = KEYS[#KEYS]
for f = 1, #KEYS - 1 do
  local args = {}
  for i = 1, n do
    args[#args + 1] = 'item' .. i
    args[#args + 1] = v
  end
  redis.call('HSET', KEYS[f], unpack(args))
end
local zargs = {}
for i = 1, n do
  zargs[#zargs + 1] = i
  zargs[#zargs + 1] = 'item' .. i
end
redis.call('ZADD', z, unpack(zargs))
return n
"""


def run() -> None:
    r = common.primary()
    log = common.RawLog(QUESTION, r,
                        note=f"{ITEMS} items x {FIELDS_PER_ITEM} hashes + 1 zset, "
                             f"{VALUE_BYTES}B values, all {len(KEYS)} keys declared in "
                             f"KEYS, single hashtag {{q2}}")
    for k in KEYS:
        r.delete(k)
    val = common.payload(VALUE_BYTES, "q2v")

    sha_per = common.load_script(r, PER_CALL)
    sha_batch = common.load_script(r, BATCHED)
    log.cmd(f"SCRIPT LOAD <{FIELDS_PER_ITEM} HSET + 1 ZADD per item> -> {sha_per}")
    log.cmd(f"SCRIPT LOAD <multi-field HSET + multi-member ZADD> -> {sha_batch}")

    rows = []
    for label, sha, ncalls in (
        (f"{ITEMS}x({FIELDS_PER_ITEM} HSET + 1 ZADD)", sha_per,
         ITEMS * (FIELDS_PER_ITEM + 1)),
        (f"{FIELDS_PER_ITEM} multi-field HSET + 1 multi-member ZADD", sha_batch,
         FIELDS_PER_ITEM + 1),
    ):
        log.cmd(f"EVALSHA {sha} {len(KEYS)} {' '.join(KEYS)} {ITEMS} <{VALUE_BYTES}B>")
        common.evalsha(r, sha, KEYS, [ITEMS, val])
        assert r.zcard(KEYS[-1]) == ITEMS
        assert r.hlen(KEYS[0]) == ITEMS
        st = common.percentiles(
            common.time_calls(lambda: common.evalsha(r, sha, KEYS, [ITEMS, val]),
                              ITERS, WARMUP))
        rows.append(common.stat_row(label, st, [ncalls, round(st["p50"] / ncalls, 2)]))

    lat_text = common.script_latency_text(r)
    log.cmd("SCRIPT LATENCY")
    table = common.format_table(
        common.STAT_HEADERS + ["redis_calls", "us_per_call(p50)"], rows)
    log.section("client latency")
    log.w(table)
    log.section("SCRIPT LATENCY (usec; includes warm-up iterations)")
    for sha, name in ((sha_per, "per-call"), (sha_batch, "batched")):
        e = common.parse_script_latency(lat_text, sha)
        log.w(f"[{name}] {sha}")
        log.w(e.get("raw", "(no histogram)"))
    log.save()

    speedup = rows[0][2] / rows[1][2]
    body = (f"{ITERS} measured iterations after {WARMUP} warm-up. All "
            f"{len(KEYS)} keys declared in `KEYS`, one hashtag `{{q2}}`.\n\n{table}\n\n"
            f"Batching collapses {ITEMS * (FIELDS_PER_ITEM + 1)} `redis.call` "
            f"crossings into {FIELDS_PER_ITEM + 1} and is **{speedup:.1f}x** faster at "
            f"p50 ({rows[0][2]:.0f}us -> {rows[1][2]:.0f}us); p99 "
            f"{rows[0][4]:.0f}us -> {rows[1][4]:.0f}us. Server-side histograms in the "
            f"raw log.\n")
    common.emit(QUESTION, "write batching (multi-field HSET / multi-member ZADD)",
                ["Q2", f"{ITEMS}x(5 HSET+1 ZADD) vs 5 multi-HSET + 1 multi-ZADD",
                 f"p50 {rows[0][2]:.0f}us -> {rows[1][2]:.0f}us ({speedup:.1f}x); "
                 f"p99 {rows[0][4]:.0f}us -> {rows[1][4]:.0f}us",
                 f"192 -> 6 redis.call is a {speedup:.1f}x win; batch writes"
                 f" (standalone run, no co-tenant; Q10 re-measures the same "
                 f"workload back to back against `--lock_on_hashtags` and reports "
                 f"a different ratio -- see the reconciliation note in Q10)"],
                body)


if __name__ == "__main__":
    run()
