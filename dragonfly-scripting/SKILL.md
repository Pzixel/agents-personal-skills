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

Dragonfly is not Redis with more threads: a script's cost is dominated by *where its keys live*, and the
same Lua costs ~27x more per `redis.call` in one regime than in the other. Establish the regime before
optimizing anything — otherwise you will optimize the wrong term.

## 0. Which regime are you in? (do this first)

| | regime (a) io-coordinated | regime (b) shard-local |
|---|---|---|
| when | default flags and the script declares keys on more than one shard — including keys that share a `{hashtag}` | one declared key, or all declared keys on one shard, or `--lock_on_hashtags` with one tag |
| cost per `redis.call` | **26.95 µs** (256 HGET, 8 keys/1 tag, 2048B values, `--proactor_threads=4`, default flags, n=200) `[lab Q5][S1]` | **1.01 µs** (same 256 calls, 1 key, atomic, same node) `[lab Q5][S1]` |
| dominant win | cutting the *number* of `redis.call` | cutting shard CPU time (payload bytes, `cjson`, loop work) |

Detect it, do not guess: `INFO ALL` before and after one invocation and diff
`eval_shardlocal_coordination_total` against `eval_io_coordination_total` — one of them goes up by 1 per
invocation and that is the answer `[df-src server_family.cc][S2]`; `bench_script.py` prints the same split.
Do not infer the regime from `dragonfly --helpfull`: it starts a new binary and prints *compiled* defaults,
so on a client host it reports `lock_on_hashtags=false` for a server that runs with it. `CONFIG GET
lock_on_hashtags` returns an empty reply on df-v1.34.0 `[lab Q9][S1]`, so the running server's flags come
from its process arguments — `ps`, the container/pod command, or the deployment spec.

A shared `{hashtag}` does **not** by itself put keys on one shard on default flags: the 8-keys-one-tag and
8-keys-eight-tags scripts both report `eval_io_coordination_total +1` and cost the same per call (26.95 vs
27.00 µs/call, same conditions) `[lab Q5][S1]`. Only `--lock_on_hashtags` makes the tag the lock/placement
unit `[df-src main_service.cc:lock_tags][S2]`.

## 1. Execution model

- One thread owns one shard; cross-thread work is message passing `[df-doc docs/df-share-nothing.md][S6]`.
- A script is one multi-transaction over the keys declared in `KEYS` `[df-doc docs/transaction.md][S5]`.
- If every declared key hashes to one shard *and* the script is atomic with declared keys, Dragonfly runs
  the whole script on that shard thread — no per-call hops `[df-src main_service.cc:CanRunSingleShardMulti][S2]`.
- Otherwise each synchronous `redis.call` forces a flush and one coordinator-to-shard hop, because Lua
  needs the reply now `[df-src main_service.cc:CallFromScript][S2]`. That hop is the 26.95 µs.
- `--!df flags=disable-atomicity` and `--!df flags=allow-undeclared-keys` both *remove* the fast path;
  `allow-undeclared-keys` promotes the script to a GLOBAL transaction that stalls every shard
  `[df-src main_service.cc:DetermineMultiMode][S2]`. Measured: under `--lock_on_hashtags`, 8 keys/1 tag goes
  1.5 µs/call atomic to 30.3 µs/call with `disable-atomicity` `[lab Q5][S1]`.
- Atomicity is real in both regimes; the fast path is a placement property, not a weaker guarantee `[S5]`.
- There is no script timeout, no `SCRIPT KILL`, no BUSY reply: an unbounded script pins its thread until
  `kill -9` `[df-doc issue #8269][S10]`. Bounding work is a correctness requirement here, not tuning.
- Only `--interpreter_per_thread` (10) scripts run per thread; the rest block on the pool
  (`lua_blocked_total`) `[df-src interpreter.cc:InterpreterManager::Get][S4]`.

## 2. Review checklist (with what each costs)

Numbers below are client p50 on df-v1.34.0, quoted with their conditions; ratios (back-to-back arms) and
absolutes (standalone runs) are never mixed in one sentence. Conditions and every cell:
`references/measurements.md`.

1. **`redis.call` per item in a loop.** 192 calls (32x(5 HSET + 1 ZADD)) to 6 multi-field calls: p50 5846 to
   539 µs, p99 9295 to 1920 µs, standalone default-flags run `[lab Q2][S1]`. Back to back, that batching is
   9.9x on default flags and 2.0x under `--lock_on_hashtags` `[lab Q10][S1]`. Run `scripts/lua_call_audit.py`
   (`call-in-loop`, `batchable-hash`).
2. **Per-item `HGET` where `HMGET` exists.** Real script (`claim_mailbox_batch.lua`, 14 keys one hashtag,
   1024 candidates, batch 32, ~2048B records): per-candidate HGET to chunked HMGET is p50 41183 to 7814 µs,
   p99 52222 to 17150 µs, replies identical, standalone default-flags `[lab Q7][S1]`; 7.6x default / 2.6x
   `--lock_on_hashtags` back to back `[lab Q10][S1]`.
3. **Batching inside a single-key script is a minor win.** One hash, 8192 HGET vs HMGET in chunks of 256:
   5496 to 4698 µs (1.2x), 0.7 µs/call `[lab Q1][S1]`. Do not spend contract risk on it in regime (b).
4. **Reads issued before the bound that rejects them.** Fetching payloads for all 1024 candidates vs the
   first 32: p50 34136 to 31971 µs (1.1x) standalone `[lab Q3][S1]`, 1.3x default / 1.8x `--lock_on_hashtags`
   back to back `[lab Q10][S1]`. Worth doing, but it is not where the win is (rule 2 is).
5. **Big `cjson.encode` on the success path.** A 1 MiB plan table encode+HSET is p50 1905 µs and 8 MiB is
   15070 µs (50 iterations); at 32 MiB the first call wrote 33.7 MB and the repeat failed with
   `Out of memory` under `--maxmemory=2048Mi`, because the old and new blob must be resident at once
   `[lab Q4][S1]`. Keep blobs off the hot path; the shard is busy for the whole encode.
6. **Unbounded loops / unbounded `unpack`.** `unpack` of 8163 fields into `redis.call` works, 8164 is a Lua
   stack overflow `[lab Q9][S1]`. Chunk at 256-1000 and bound the loop by an ARGV-supplied limit.
7. **Undeclared keys** (key names built by concatenation instead of taken from `KEYS`): a hard error by
   default, a server-wide GLOBAL transaction when enabled `[df-src DetermineMultiMode][S2][S7]`.
8. **`--!df flags=` used as an optimization.** It is the opposite: both real flags kill the fast path
   `[lab Q5][S1]`, and `no-writes` is parsed and ignored `[df-src script_mgr.cc][S3]`.
9. **Discarded `redis.call` results.** Switching 256 discarded HSETs to `redis.acall` (or `--lua_auto_async`,
   atomic scripts only) is 28.05 to 2.93 µs/call, 8 keys/1 tag `[lab Q5][S1]`; reads are unchanged.
10. **`SCRIPT LOAD` per worker boot.** Fresh text vs re-loading an already-cached sha, 8894B script, default
    flags: p50 499 vs 162 µs idle; 478 vs 149 µs under 4 loaders `[lab Q11d][S1]`; it borrows an interpreter from the
    per-thread pool `[df-doc PR #8300][S11]`. Load once per process, not once per worker.

11. **Writes the script has already proven redundant.** Deleting and re-inserting unchanged members,
    or rewriting a record after proving it byte-identical, is not free just because the value does not
    change: every such write still costs a shard mutation, journal bytes and replica bytes — a replica adds
    +4.3% (per-call) / +41.6% (batched) p50 to a write script `[lab Q6][S1]`. Once the script has compared
    old and new, skip the write.

`scripts/lua_call_audit.py` mechanizes 1, 2, 4, 5 and 7 (`--rules`, `--rule`, `--exclude`, `--count`). Treat
its findings as candidates to measure, not defects.

## 3. Designing a new operation

1. **Declare every key in `KEYS`**, in a fixed order, documented in a header comment; pin the arity to the
   script identity (BullMQ puts it in the filename: `moveToFinished-9.lua`) `[S26][S28]`.
2. **Decide placement before syntax.** Keys the script must see together want one `{hashtag}`; then check
   the tag's *cardinality* against the shard count, because a tag is a heat unit `[df-doc][S15]`. Under
   `--cluster_mode=yes` one slot per script is a correctness requirement, not tuning `[df-doc][S16]`.
3. **Batch by construction**: one multi-field `HMGET`/`HSET`/`HDEL`, one multi-member `ZADD`, chunked at
   256-1000 fields `[lab Q2, Q7, Q9][S1]`. Prefer `redis.acall` for calls whose reply you ignore `[lab Q5][S1]`.
4. **Bound the work** with an explicit ARGV limit and return a "more available" marker; there is no
   server-side escape hatch `[df-doc issue #8269][S10]`.
5. **Shape the reply** as a flat array with integer status codes and constant arity; `redis.setresp` does
   not exist here, so RESP2 conversion (truncation at the first `nil`, floats to integers) always applies
   `[lab Q9][S1][semantics][S21][S26]`.
6. **Ask whether it should be Lua at all.** Rate limiting has a native single-key command: a 4-`redis.call`
   Lua sliding window costs 0.9x `CL.THROTTLE` on default flags and 0.8x under `--lock_on_hashtags` (one
   key, n=200) — the Lua version buys nothing but maintenance `[lab Q11c][S1][S13]`.

`--lock_on_hashtags` is a measured trade-off, never a default: it buys the shard-local path for a
one-hashtag script (26.95 to 1.54 µs/call) `[lab Q5][S1]` and pays for it by serialising unrelated work
behind that tag's shard `[lab Q11d][S1]`. **Rule:** consider it only if distinct tags are at least the shard
count, or the workload's total script CPU fits one core; then measure both arms at the real concurrency. The
measured trade-off and the whole flag surface: `references/server-flags.md`.

## 4. Measuring

Never ship a script change on reasoning alone: Q10 measured the *same* three workloads twice on default
flags and got 1.3-2.2x different absolutes depending on co-tenancy `[lab Q10][S1]`.

1. `scripts/lab.sh up` starts the lab; `--single` adds a 1-proactor node, which separates "slow script" from
   "slow because it fans out".
2. Capture the regime first: `bench_script.py` reports the coordination-counter delta per invocation
   alongside client p50/p95/p99 and the server's `SCRIPT LATENCY` entry for that sha.
3. **Any before/after latency claim** must come from identical seeded state, with a reply-equality check and
   both p50 and p99 — numbers from different state, or from an unreseeded second-call path, are not a
   comparison. `scripts/bench_script.py --spec <spec.json> --seed <seed.py> --reseed --compare <orig>.lua
   <new>.lua` does exactly that: `--compare` exits 2 unless the replies are byte-identical outside
   `ignore_reply_indices` (server-clock fields), and `--reseed` re-runs the seed before every call, untimed.
   A worked pair — spec, seed and two variants of a real claim script — is in `assets/examples/`; the
   invocation and its recorded numbers are in `references/measurements.md`.
4. Server-side view under load: `scripts/script_latency.py --sha <sha> --watch`. The histograms are
   cumulative for the server's lifetime and `SCRIPT FLUSH` does **not** reset an existing sha's histogram:
   the sample count carries across the flush and a reload of identical text `[lab Q9][S1]`. For a clean
   before/after, difference the counters around the window (`--watch`) or restart the node; `SCRIPT STATS`
   does not exist and
   `SCRIPT FLAGS` is a setter `[lab Q9][S1]`. Percentiles are read off bucket upper bounds (8-16 buckets),
   so quote them as bounds, not as precise percentiles.
5. Report client p99 too. A change that improves p50 and worsens p99 is common here (Q7: p50 5.3x, p99 3.0x).

**Contract before speed.** Batch every read the original performs and keep its whole-window checks;
narrowing a check to the items you actually claim changes the contract, so it is the data owner's decision,
not a rewrite you ship for the 1.1-1.8x it adds `[lab Q3][S1]`. Details: `references/lua-patterns.md §2`.

## 5. References

- `references/execution-model.md` — read when a counter, hop count or multi-mode needs explaining, or you
  are citing source for the fast path, squashing, `acall` or the interpreter pool.
- `references/lua-patterns.md` — read when rewriting a script body: batching idioms, chunk sizes, cjson,
  reply and error protocol, the `[semantics]` definition.
- `references/measurements.md` — read before quoting any number, or to run the worked comparison: the
  Q1-Q11 table, lab conditions, what not to quote.
- `references/server-flags.md` — read when a flag or `--!df` directive is proposed, or you need the v1.34.0
  default and the docs-vs-source conflicts.
- `references/redis-advice-audit.md` — read when the user proposes standard Redis tuning advice.
- `references/redis-differences.md` — read when porting from Redis, or a command/library the user quotes
  behaves differently or is missing here.
- `references/sources.md` — read when resolving an `[S<n>]` tag.
