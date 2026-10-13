---
name: dragonfly-scripting
description: >-
  Read before any Dragonfly (dragonflydb) server-side scripting work,
  including porting scripts from Redis and choosing between scripts,
  pipelines, MULTI and native commands (e.g. rate limiting, leases, claims,
  queues). Covers Lua, EVAL/EVALSHA, redis.call loops, SCRIPT LOAD,
  SCRIPT LATENCY, SCRIPT FLAGS, script INFO counters
  (eval_*_coordination_total, lua_*), script latency/p99, and script-related
  hashtags, shard placement, --lock_on_hashtags and --!df flags. Read even
  when Redis knowledge seems sufficient: Dragonfly's execution model, flag
  syntax and metrics differ. Skip Redis-only work and Dragonfly tasks
  unrelated to scripting (deployment, replication, memory internals).
---

# Dragonfly scripting

A script's cost is dominated by *where its keys live*; establish the regime before optimizing anything.

## 0. Regime first

`INFO ALL` before and after one invocation; whichever of `eval_shardlocal_coordination_total` and
`eval_io_coordination_total` rises by 1 is the regime. Regime (a)
io-coordinated (keys on more than one shard, including keys that share a `{hashtag}` on default flags): each
`redis.call` costs ~27 µs, so cut the *number* of calls. Regime (b) shard-local (one key, or one shard, or
`--lock_on_hashtags` with one tag): ~1 µs per call, so cut shard CPU (payload bytes, `cjson`, loop work).
Read the running server's flags from its process arguments, never from `dragonfly --helpfull` or `CONFIG
GET`.

## 1. Review checklist

Client p50 on df-v1.34.0.

1. **`redis.call` per item in a loop** → multi-field calls: 9.9x on default flags, 2.0x under
   `--lock_on_hashtags` `[lab Q10][S1]`.
2. **Per-item `HGET` where `HMGET` exists** → chunked `HMGET`: 7.6x default / 2.6x `--lock_on_hashtags`
   `[lab Q10][S1]`.
3. **Batching inside a single-key script**: 1.2x `[lab Q1][S1]`; not worth contract risk.
4. **Reads issued before the bound that rejects them** → move the bound up: 1.1-1.8x `[lab Q3, Q10][S1]`.
5. **Big `cjson.encode` on the success path**: 8 MiB is 15 ms of busy shard; 32 MiB ran out of
   memory `[lab Q4][S1]`. Keep blobs off the hot path.
6. **Unbounded loops / `unpack`**: 8164 fields overflow the Lua stack `[lab Q9][S1]`; no script timeout exists. Chunk at 256-1000 and bound loops by an ARGV limit.
7. **Undeclared keys**: a hard error by default, a server-wide GLOBAL transaction when enabled `[S2][S7]`.
8. **`--!df flags=` used as an optimization**: both real flags kill the fast path; `no-writes` is ignored
   `[lab Q5][S1][S3]`.
9. **Discarded `redis.call` results** → `redis.acall`: 28.05 to 2.93 µs/call `[lab Q5][S1]`.
10. **`SCRIPT LOAD` per worker boot**: p50 499 vs 162 µs; load once per process `[lab Q11d][S1]`.
11. **Writes already proven redundant**: each still costs a shard mutation and journal and replica bytes
    `[lab Q6][S1]`; once the script has compared old and new, skip the write.

`scripts/lua_call_audit.py` mechanizes 1, 2, 4, 5 and 7; its findings are candidates to measure, not defects.
`--lock_on_hashtags` is a measured trade-off, never a default: consider it only if distinct tags are at least
the shard count, or total script CPU fits one core `[lab Q11d][S1]`.

## 2. Verify against Dragonfly docs or measurement

Never ship a script change, or apply Redis advice, on reasoning alone: verify it in Dragonfly docs or source,
or measure it. A latency claim comes from `scripts/bench_script.py --spec <spec.json> --seed <seed.py>
--reseed --compare <orig>.lua <new>.lua` (exits 2 unless replies are byte-identical) and reports p50 and p99.
Batch every read the original performs and keep its whole-window checks; narrowing a check changes the
contract and is the data owner's decision.

## 3. References

In `references/`: `regime-and-design.md` (regime detail, execution model, new-operation design, measuring
procedure), `execution-model.md` (source citations), `lua-patterns.md` (rewrite idioms), `measurements.md`
(read before quoting any number), `server-flags.md` (any proposed flag), `redis-advice-audit.md` (Redis
tuning advice), `redis-differences.md` (porting), `sources.md` (`[S<n>]` tags).

Read `regime-and-design.md` when designing a new operation or choosing a regime.
