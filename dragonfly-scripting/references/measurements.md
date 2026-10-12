# Measurements — what was measured, under what conditions, and how to redo it

This file is the table of every number the skill quotes (Q1-Q11) and the conditions each was taken under
`[S1]`. Quote cells from here; do not re-derive a magnitude from an ad-hoc run.

## Lab conditions (apply to every number unless a row says otherwise)

- Dragonfly **v1.34.0** (`redis_version 7.4.0`) in Docker, image pinned by digest in `assets/compose.yaml`.
- Primary `:6379` with `--cache_mode=false --maxmemory=2048Mi --dbfilename=dump --proactor_threads=4`;
  replica `:6380`; optional single-proactor node `:6381` (`lab.sh up --single`). Throwaway nodes for flag
  arms run on `:6382` with the same flags plus the flag under test.
- `CONFIG GET lock_on_hashtags` returns an empty reply on df-v1.34.0 (observed 2026-09-16 against the lab
  primary), so the flag cannot be read back from a running server: use the process arguments and the
  coordination-counter diff.
- 4-core host. Anything measured while a throwaway node's 4 busy-polling proactors are up carries a
  co-tenant; this is the reason Q10 and the standalone runs disagree (below).
- 200 timed iterations after 20 warm-up (Q4: 50 after 3; Q8: 300 probes after 30), client-side wall time of
  `EVALSHA`, p50 unless stated. Values 2048B unless stated.
- A "µs/call" figure is that p50 divided by the script's `redis.call` count, not a per-call percentile.

## Summary

| Q | what was varied | key numbers (conditions in the cell) | what it licenses |
|---|---|---|---|
| Q1 | N x `HGET` vs `HMGET`/256 in a **single hash** (shard-local) | N=8192: p50 5496 -> 4698 µs (1.2x); per-call 0.7 µs at N=8192, 5.1 µs at N=32 | batching is a minor win once you are shard-local |
| Q2 | 32x(5 `HSET`+1 `ZADD`) = 192 calls vs 6 multi-arg calls, one hashtag, standalone | p50 5846 -> 539 µs, p99 9295 -> 1920 µs (10.8x standalone) | absolute cost of write batching |
| Q3 | 1024 candidates, payload for all vs first 32, standalone | p50 34136 -> 31971 µs (1.1x); the metadata scan still costs 31971 µs | deferring payload reads is real but small |
| Q4 | `cjson.encode` of a 1/8/32 MiB table + `HSET`(+`HDEL`), 50 iters | 1 MiB p50 1905 µs; 8 MiB 15070 µs; 32 MiB wrote 33766783 B once then `-ERR Out of memory` at `--maxmemory=2048Mi` | encode cost and the 2x-resident rewrite trap. **Its concurrent-`GET` column is run-to-run unstable — do not quote it** |
| Q5 | 256 `HGET`, 1 key vs 8 keys/1 tag vs 8 keys/8 tags, x {atomic, disable-atomicity, allow-undeclared-keys} x {default, `--lock_on_hashtags`, `--lua_auto_async`} | per call: 1.01 µs (1 key, atomic, default) / 26.95 µs (8k, 1 tag) / 27.00 µs (8k, 8 tags); hop probe 25.26 vs 23.17 µs (1 vs 4 tags); under `--lock_on_hashtags` 8k/1tag atomic 1.54 µs vs 30.3 µs with `disable-atomicity` | the whole regime split, and the fast-path killers |
| Q6 | Q2 scripts, primary with replica attached vs detached | per-call 4679 vs 4484 µs (+4.3%), batched 569 vs 402 µs (+41.6%), mean of p50s | replication cost lands hardest on already-cheap scripts |
| Q7 | real `claim_mailbox_batch.lua`, 1024 due, batch 32, ~2048B records, 14 keys/1 tag, standalone | p50 41183 -> 7814 µs (5.3x), p99 52222 -> 17150 µs, replies identical; `tx_shard_polls` +663192 vs +49792 over 200 invocations | the headline rewrite, on a production script |
| Q8 | 8 loader processes x ~2 ms script, 9th client `GET` p99, threads 1 vs 4, probe in same vs different tag | p99 33219 (t1/same), 33084 (t1/diff), 169 (t4/same), 191 µs (t4/diff) | head-of-line blocking is a proactor-count problem on default flags; the tag is irrelevant there |
| Q9 | inventory of `SCRIPT *`, `DEBUG`, `INFO`, directives, Lua libs, 20 server flags | `SCRIPT LATENCY`/`FLAGS`/`LIST`/`GC` exist, `SCRIPT STATS`/`KILL`/`FUNCTION`/`FCALL` do not; `#!lua` is a **syntax error**; Lua 5.4; `unpack` ceiling 8163 | what exists on v1.34.0 |
| Q10 | Q2/Q3/Q7 re-run on default vs `--lock_on_hashtags` **back to back in one session** | batching 9.9x -> 2.0x; payload skip 1.3x -> 1.8x; `claim_mailbox_batch` 7.6x -> 2.6x; counters flip io -> shardlocal | **the ratios**, and how much of each win survives the flag |
| Q11 | (a) script vs pipeline vs MULTI, (b) many keys vs one hash, (c) Lua limiter vs `CL.THROTTLE`, (d) `SCRIPT LOAD` under load | (a) 64 `GET`: script/pipeline 3.9x default, 0.4x lock; (b) 100k x 64B: 83.7 B/value as strings vs 109.6 in one hash; (c) Lua 0.9x / 0.8x `CL.THROTTLE`; (d) fresh `SCRIPT LOAD` 499 µs idle -> 478 µs under 4 loaders default, 440 -> 7787 µs under lock | the Redis-advice audit |

## The one rule about quoting these numbers

Q10 re-measured Q2/Q3/Q7 on default flags and got different absolutes than the standalone runs (Q2 5846 vs
3936 µs, Q3 34136 vs 74627 µs — 2.2x apart, Q7 41183 vs 54947 µs), because the throwaway `--lock_on_hashtags`
container's 4 busy-polling proactors were already running as a co-tenant on the 4-core host during Q10's
default arm `[lab Q10][S1]`.

**Take ratios from Q10 (both arms measured back to back under the same co-tenancy) and absolutes from the
standalone Q2/Q3/Q7 runs. Never mix them in one sentence.** And always carry the conditions in the same
clause: a 1-key shard-local number and an 8-key io-coordinated number are not comparable.

Q11 recorded only p50/p99 per variant; quote no other percentile from it. Q11(d)'s counter deltas are
server-wide (4 concurrent loaders), so no execution path can be inferred from them `[lab Q11][S1]`.

## Redoing it on your workload

1. `scripts/lab.sh up` (`--single` also starts the 1-proactor node, which separates "slow script" from
   "slow because it fans out across shards"); `lab.sh status` prints container state, PING and version;
   `lab.sh down` removes containers **and volumes**.
2. Write a spec and a seed for `scripts/bench_script.py`. The spec is the invocation only —
   `{"keys": [...], "argv": [...], "iters": 200, "warmup": 20, "ignore_reply_indices": [...]}` — and the
   seed is your own program run as `<seed> <host> <port>`; if it prints a JSON object on stdout that object
   is merged into the spec, which is how fresh tokens or a run id read from `INFO` get in.
3. `--reseed` runs the seed before **every** call, untimed. A non-idempotent script measured without it is
   measured on its second-call path (for a claim/lease script: the path where there is nothing left to claim).
4. `--compare a.lua b.lua` asserts the replies are byte-identical outside `ignore_reply_indices` before
   reporting anything, and exits 2 if they are not. Use `ignore_reply_indices` only for server-clock fields,
   and check a derived invariant instead (Q7 checked `deadline - now == 120000`) `[lab Q7][S1]`.
5. Read the coordination split `bench_script.py` prints per invocation — that is the regime, and it is the
   first thing to look at, before any latency number.

Worked example, complete in `assets/examples/`: `claim_mailbox_batch_spec.json` (30 iterations, 5 warm-up,
KEYS/ARGV from the seed), `claim_mailbox_batch_seed.py` (1024 mailboxes under `{inbound}`), and
the two variants `claim_mailbox_batch.orig.lua` and `claim_mailbox_batch.hmget.lua`:

```
scripts/bench_script.py --spec assets/examples/claim_mailbox_batch_spec.json \
  --seed assets/examples/claim_mailbox_batch_seed.py --reseed \
  --compare assets/examples/claim_mailbox_batch.orig.lua assets/examples/claim_mailbox_batch.hmget.lua
```

`--reseed` is required here: this is a claim/lease script, so without it the second call onwards measures
the nothing-left-to-claim path. On this pair `--compare` asserted the replies identical outside the declared
server-clock positions, and both variants reported io coordination 1.00/invocation. Quote the magnitude from
the recorded Q7 cell — p50 41183 to 7814 µs, p99 52222 to 17150 µs, standalone default flags `[lab Q7][S1]`
— not from an ad-hoc re-run: the ratio is stable, the absolutes move with co-tenancy (see the Q10 note
above).

## Server-side latency

`scripts/script_latency.py` parses `SCRIPT LATENCY` (microseconds; the histogram is cumulative for the
**server's lifetime**, and nothing short of restarting the node resets it — `SCRIPT FLUSH` does not, see
below; `SCRIPT STATS` does not exist and `SCRIPT FLAGS` is a setter, not a getter)
`[lab Q9][S1]`. `--sha` filters, `--watch` prints the delta since the previous sample, which is what you
want while a load test runs: differencing the counters before and after the window is the only way to get a
clean before/after from this histogram.

### `SCRIPT FLUSH` does not reset `SCRIPT LATENCY`

The histogram survives `SCRIPT FLUSH` verbatim, and a run after reloading identical text continues the same
count: 5 runs, `SCRIPT FLUSH`, reload, 1 run leaves `Count: 6`, not `Count: 1` (`redis-cli` against the lab
primary, df-v1.34.0, 2026-09-16) `[lab Q9][S1]`. So a `SCRIPT LATENCY` number is a before/after measurement
only if you difference two samples (`scripts/script_latency.py --watch`) or restart the node between arms.

Caveat: p50/p95/p99 are read off the cumulative-percent column, so each is the **upper bound of the bucket
that crosses that percentile**, at 8-16 buckets of resolution. `--watch` deltas have no server-reported
median, so the p50 cell degrades to `p50<=<bucket upper bound>`. Quote these as bounds; for percentiles that
carry decisions, use the client-side numbers from `bench_script.py`, and report p99 next to p50 — Q7
improved p50 5.3x but p99 only 3.0x `[lab Q7][S1]`.

## Static audit as a triage step

`scripts/lua_call_audit.py <file-or-dir>` prints `path:line: rule-id: problem -> fix` and always exits 0.
Rules: `call-in-loop`, `batchable-hash`, `read-past-bound`, `cjson-hot`, `undeclared-key`; `--rule`/`--rules`
/`--exclude` select them and `--count` summarizes. Its output is a list of things to **measure**, not a list
of defects: Q1 shows that in a single-key script the same shape is worth only 1.2x `[lab Q1][S1]`.
