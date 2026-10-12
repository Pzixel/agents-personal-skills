#!/usr/bin/env python
"""Q3 — read-after-full vs early stop.

ZRANGEBYSCORE yields 1024 candidates. Variant A reads version + lease + ~2 KB
payload for every candidate. Variant B reads version + lease for every candidate
(metadata is still read for all 1024) but fetches the payload only for the first
32 that qualify. Same selection rule, different payload volume."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common

QUESTION = "q3"
CANDIDATES = 1024
BATCH = 32
PAYLOAD_BYTES = 2048
ITERS = 200
WARMUP = 20
KEYS = ["{q3}due", "{q3}ver", "{q3}lease", "{q3}payload"]

FULL = """
local zkey, vkey, lkey, pkey = KEYS[1], KEYS[2], KEYS[3], KEYS[4]
local window = tonumber(ARGV[1])
local ids = redis.call('ZRANGEBYSCORE', zkey, '-inf', ARGV[2], 'LIMIT', 0, window)
local bytes = 0
local chosen = 0
for i = 1, #ids do
  local ver = redis.call('HGET', vkey, ids[i])
  local lease = redis.call('HGET', lkey, ids[i])
  local body = redis.call('HGET', pkey, ids[i])
  if body then bytes = bytes + #body end
  if not lease then chosen = chosen + 1 end
end
return {#ids, chosen, bytes}
"""

EARLY = """
local zkey, vkey, lkey, pkey = KEYS[1], KEYS[2], KEYS[3], KEYS[4]
local window = tonumber(ARGV[1])
local want = tonumber(ARGV[3])
local ids = redis.call('ZRANGEBYSCORE', zkey, '-inf', ARGV[2], 'LIMIT', 0, window)
local bytes = 0
local chosen = 0
for i = 1, #ids do
  local ver = redis.call('HGET', vkey, ids[i])
  local lease = redis.call('HGET', lkey, ids[i])
  if not lease and chosen < want then
    local body = redis.call('HGET', pkey, ids[i])
    if body then bytes = bytes + #body end
    chosen = chosen + 1
  elseif not lease then
    chosen = chosen + 1
  end
end
return {#ids, chosen, bytes}
"""


def seed(r) -> None:
    for k in KEYS:
        r.delete(k)
    blob = common.payload(PAYLOAD_BYTES, "q3payload")
    pipe = r.pipeline(transaction=False)
    for i in range(CANDIDATES):
        mid = f"m{i}"
        pipe.zadd(KEYS[0], {mid: i})
        pipe.hset(KEYS[1], mid, "1")
        pipe.hset(KEYS[3], mid, blob)
        if i % 200 == 0:
            pipe.execute()
            pipe = r.pipeline(transaction=False)
    pipe.execute()


def run() -> None:
    r = common.primary()
    log = common.RawLog(QUESTION, r,
                        note=f"{CANDIDATES} due candidates, no leases, "
                             f"{PAYLOAD_BYTES}B payloads, batch size {BATCH}")
    seed(r)
    log.cmd(f"ZADD {KEYS[0]} 0..{CANDIDATES - 1} mN ; HSET {KEYS[1]}/{KEYS[3]} "
            f"(seed, {PAYLOAD_BYTES}B payloads)")

    sha_full = common.load_script(r, FULL)
    sha_early = common.load_script(r, EARLY)
    log.cmd(f"SCRIPT LOAD <payload for all {CANDIDATES}>   -> {sha_full}")
    log.cmd(f"SCRIPT LOAD <payload for first {BATCH} only> -> {sha_early}")

    rows = []
    for label, sha, args, want_bytes in (
        (f"payload for all {CANDIDATES}", sha_full, [CANDIDATES, CANDIDATES],
         CANDIDATES * PAYLOAD_BYTES),
        (f"payload for first {BATCH}", sha_early, [CANDIDATES, CANDIDATES, BATCH],
         BATCH * PAYLOAD_BYTES),
    ):
        log.cmd(f"EVALSHA {sha} 4 {' '.join(KEYS)} {' '.join(str(a) for a in args)}")
        n, chosen, got = common.evalsha(r, sha, KEYS, args)
        assert n == CANDIDATES and got == want_bytes, (label, n, chosen, got)
        log.w(f"  -> candidates={n} chosen={chosen} payload_bytes={got}")
        st = common.percentiles(
            common.time_calls(lambda: common.evalsha(r, sha, KEYS, args),
                              ITERS, WARMUP))
        rows.append(common.stat_row(label, st,
                                    [CANDIDATES * 3 if "all" in label
                                     else CANDIDATES * 2 + BATCH,
                                     round(want_bytes / 1024)]))

    lat_text = common.script_latency_text(r)
    log.cmd("SCRIPT LATENCY")
    table = common.format_table(
        common.STAT_HEADERS + ["redis_calls", "payload_KiB"], rows)
    log.section("client latency")
    log.w(table)
    log.section("SCRIPT LATENCY (usec; includes warm-up iterations)")
    for sha, name in ((sha_full, "read-after-full"), (sha_early, "early stop")):
        e = common.parse_script_latency(lat_text, sha)
        log.w(f"[{name}] {sha}")
        log.w(e.get("raw", "(no histogram)"))
    log.save()

    speedup = rows[0][2] / rows[1][2]
    body = (f"{ITERS} measured iterations after {WARMUP} warm-up. Metadata "
            f"(version + lease) is read for all {CANDIDATES} candidates in both "
            f"variants; only the payload reads differ "
            f"({CANDIDATES} vs {BATCH} x {PAYLOAD_BYTES}B).\n\n{table}\n\n"
            f"Dropping {CANDIDATES - BATCH} payload `HGET`s "
            f"({(CANDIDATES - BATCH) * PAYLOAD_BYTES // 1024} KiB) takes p50 from "
            f"{rows[0][2]:.0f}us to {rows[1][2]:.0f}us (**{speedup:.1f}x**), p99 "
            f"{rows[0][4]:.0f}us -> {rows[1][4]:.0f}us. The remaining "
            f"{CANDIDATES * 2} metadata calls dominate the residual cost.\n")
    common.emit(QUESTION, "read-after-full vs stop after the batch",
                ["Q3", f"{CANDIDATES} candidates, payload for all vs first {BATCH}",
                 f"p50 {rows[0][2]:.0f}us -> {rows[1][2]:.0f}us ({speedup:.1f}x); "
                 f"p99 {rows[0][4]:.0f}us -> {rows[1][4]:.0f}us",
                 f"fetch payloads only for the chosen {BATCH}; metadata scan still "
                 f"costs {rows[1][2]:.0f}us"
                 f" (standalone run, no co-tenant; Q10 re-measures the same "
                 f"workload back to back against `--lock_on_hashtags` and reports "
                 f"a different ratio -- see the reconciliation note in Q10)"],
                body)


if __name__ == "__main__":
    run()
