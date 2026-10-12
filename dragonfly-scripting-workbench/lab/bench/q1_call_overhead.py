#!/usr/bin/env python
"""Q1 — redis.call overhead inside Lua: N single HGETs vs HMGET in chunks of 256.

One hash, N fields of ~2 KB. N in {32, 256, 1024, 8192}. Both variants read the
same bytes and return the same total length; only the number of redis.call()
crossings differs."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common

QUESTION = "q1"
NS = [32, 256, 1024, 8192]
FIELD_BYTES = 2048
CHUNK = 256
ITERS = 200
WARMUP = 20
KEY = "{q1}h"

PER_CALL = """
local n = tonumber(ARGV[1])
local total = 0
for i = 1, n do
  local v = redis.call('HGET', KEYS[1], 'f' .. i)
  if v then total = total + #v end
end
return total
"""

CHUNKED = """
local n = tonumber(ARGV[1])
local chunk = tonumber(ARGV[2])
local total = 0
local i = 1
while i <= n do
  local fields = {}
  local last = math.min(i + chunk - 1, n)
  for j = i, last do fields[#fields + 1] = 'f' .. j end
  local vals = redis.call('HMGET', KEYS[1], unpack(fields))
  for k = 1, #vals do
    if vals[k] then total = total + #vals[k] end
  end
  i = last + 1
end
return total
"""


def seed(r, n_max: int) -> None:
    r.delete(KEY)
    blob = common.payload(FIELD_BYTES, "q1payload")
    pipe = r.pipeline(transaction=False)
    for i in range(1, n_max + 1):
        pipe.hset(KEY, f"f{i}", blob)
        if i % 1000 == 0:
            pipe.execute()
            pipe = r.pipeline(transaction=False)
    pipe.execute()


def run() -> None:
    r = common.primary()
    log = common.RawLog(QUESTION, r,
                        note=f"one hash {KEY}, {FIELD_BYTES}B values, "
                             f"{WARMUP} warm-up + {ITERS} measured iterations per cell")
    seed(r, max(NS))
    log.cmd(f"HSET {KEY} f1..f{max(NS)} <{FIELD_BYTES} bytes>  (seed)")

    sha_call = common.load_script(r, PER_CALL)
    sha_chunk = common.load_script(r, CHUNKED)
    log.cmd(f"SCRIPT LOAD <per-call HGET loop>   -> {sha_call}")
    log.cmd(f"SCRIPT LOAD <HMGET chunks of {CHUNK}> -> {sha_chunk}")

    rows, ratio_rows = [], []
    for n in NS:
        for label, sha, args in (
            (f"N={n} per-call HGET", sha_call, [n]),
            (f"N={n} HMGET/{CHUNK}", sha_chunk, [n, CHUNK]),
        ):
            log.cmd(f"EVALSHA {sha} 1 {KEY} {' '.join(str(a) for a in args)}")
            expect = n * FIELD_BYTES
            got = common.evalsha(r, sha, [KEY], args)
            assert got == expect, f"{label}: returned {got}, expected {expect}"
            st = common.percentiles(
                common.time_calls(lambda: common.evalsha(r, sha, [KEY], args),
                                  ITERS, WARMUP))
            calls = n if "per-call" in label else -(-n // CHUNK)
            rows.append(common.stat_row(label, st, [calls, round(st["p50"] / calls, 2)]))
        per, chunked = rows[-2], rows[-1]
        ratio_rows.append([n, per[2], chunked[2], round(per[2] / chunked[2], 1),
                           per[-1], chunked[-1]])

    lat_text = common.script_latency_text(r)
    log.cmd("SCRIPT LATENCY")
    table = common.format_table(
        common.STAT_HEADERS + ["redis_calls", "us_per_call(p50)"], rows)
    ratio = common.format_table(
        ["N", "per-call p50 us", "HMGET p50 us", "speedup x",
         "per-call us/redis.call", "HMGET us/redis.call"], ratio_rows)
    log.section("per-variant client latency")
    log.w(table)
    log.section("comparison")
    log.w(ratio)
    log.section("SCRIPT LATENCY (server-side, usec; includes warm-up iterations)")
    for sha, name in ((sha_call, "per-call HGET"), (sha_chunk, f"HMGET/{CHUNK}")):
        e = common.parse_script_latency(lat_text, sha)
        log.w(f"[{name}] {sha}")
        log.w(e.get("raw", "(no histogram)"))
    log.save()

    worst = ratio_rows[-1]
    body = (f"{ITERS} measured iterations after {WARMUP} warm-up, per cell. "
            f"Both variants return the same byte total (asserted). The workload is "
            f"a SINGLE hash, i.e. the shard-local execution path; Q5 measures the "
            f"same 256-call shape across 8 keys at 26.95us/`redis.call`, ~40x more, "
            f"so the per-call figures below apply to single-key scripts only."
            f"\n\n{ratio}\n\n"
            f"Per-variant client latency:\n\n{table}\n\n"
            f"Server-side `SCRIPT LATENCY` histograms for both shas are in the raw log "
            f"(they include the {WARMUP} warm-up iterations).\n")
    common.emit(QUESTION, "redis.call overhead inside Lua",
                ["Q1", f"N x HGET vs HMGET/{CHUNK}, {FIELD_BYTES}B values",
                 f"N=8192: {worst[1]:.0f}us vs {worst[2]:.0f}us p50 "
                 f"({worst[3]}x); {worst[4]}us per redis.call against ONE key",
                 f"against one key (shard-local) a redis.call costs ~{worst[4]}us "
                 f"at N=8192 and {ratio_rows[0][4]:.1f}us at N=32; across 8 keys "
                 f"Q5 measures 26.95us/call -- batch with HMGET"],
                body)


if __name__ == "__main__":
    run()
