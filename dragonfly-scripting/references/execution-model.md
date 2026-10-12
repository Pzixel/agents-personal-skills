# Execution model — how Dragonfly runs a Lua script (v1.34.0)

Source tags: `[df-src …]` = the v1.34.0 checkout, `[df-doc …]` = docs/blog/issue, `[lab Qn]` = measured in
this repo's lab. `[S<n>]` rows are in `sources.md`.

## Threads, shards, hops

- Each database shard is owned and accessed by a single thread; inter-thread interaction is message passing
  only `[df-doc docs/df-share-nothing.md][S6]`.
- A **hop** is one blocking coordinator-to-shard round trip. "the coordinator sends a message, it blocks
  until it gets an answer. We call such interaction a *message hop*" `[df-doc docs/transaction.md][S5]`.
  Only the coordinator *fiber* blocks; its thread keeps serving other fibers `[S5]`.
- A transaction has two phases: scheduling, then one or more execution hops. The coordinator waits for
  **all** shards before the next hop `[S5]`.
- Ordering uses a global atomic sequence counter (VLL-derived); the doc itself calls it "a source of
  contention - it breaks the shared nothing model" `[S5]`. A concluding, non-global, **single-shard**
  transaction executes optimistically inline and skips that counter
  `[df-src transaction.cc:738 ScheduleInternal][S2]`.
- Locks are *intent* counters (`key -> counter`), keyed by 64-bit fingerprints of a `LockTag`, not blocking
  mutexes; being scheduled does not mean holding exclusivity `[S5][S30]`.
- Caveat: the `main` branch version of `docs/transaction.md` is a post-v1.34.0 rewrite; the shipped v1.34.0
  doc leaves the optimization section as `TBD`, so for v1.34.0 behavior the code is authoritative `[S5]`.

## The three multi-modes, and which one your script gets

`DetermineMultiMode` derives the mode from exactly two script flags
`[df-src main_service.cc:DetermineMultiMode][S2]`:

| flags | mode | consequence |
|---|---|---|
| atomic, declared keys (**default**) | `LOCK_AHEAD` | scheduled on exactly the declared keys; the only mode eligible for the single-shard fast path |
| atomic + `allow-undeclared-keys` | `GLOBAL` | takes the shard-level lock on **every** shard; "it prevents Dragonfly from running concurrently and thus greatly decreases throughput" `[S5]` |
| `disable-atomicity` | `NON_ATOMIC` | every command is its own transaction; better under contention, no atomicity, no fast path |

## The single-shard fast path

- `EvalInternal`: "If script runs on a single shard, we run it remotely to save hops"
  `[df-src main_service.cc:EvalInternal][S2]`.
- Eligibility: `CanRunSingleShardMulti` returns false unless the mode is `LOCK_AHEAD`
  `[df-src main_service.cc:CanRunSingleShardMulti][S2]`. So either `--!df` flag disables it.
- The shard id is computed from the **declared** `KEYS` only; one key on another shard collapses the path
  `[df-src EvalInternal][S2]`.
- The connection may be migrated to the shard's thread (`--migrate_connections`, default true, "only
  supported for Lua script invocations, and can happen at most once per connection") `[df-doc][S8]`.
- Keys are locked by `LockTag`, not by the raw key — the hook `--lock_on_hashtags` uses
  `[df-src main_service.cc lock_tags][S2][S30]`.
- On the fast path squashing is explicitly disabled, because the squashing mechanism is what runs the script
  remotely: "args.async = false" `[df-src EvalInternal][S2]`.

**Measured consequence.** Holding call count (256) and value size (2048B) constant on the lab primary
(`--proactor_threads=4`, default flags, n=200 after 20 warm-up): 1 key atomic = **1.01 µs** per `redis.call`
and `eval_shardlocal_coordination_total +1` per invocation; 8 keys in one hashtag = **26.95 µs/call** and
`eval_io_coordination_total +1`; 8 keys in eight hashtags = **27.00 µs/call**, also io. A hop microbenchmark
(256 `GET` on 256 distinct keys) gives 25.26 µs/call for one hashtag vs 23.17 µs/call for four `[lab Q5][S1]`.
The hashtag is not the lever on default flags; the shard count of the declared key set is.

With `--lock_on_hashtags` on an otherwise identical throwaway node, the 8-keys-one-tag script flips to
`eval_shardlocal_coordination_total +1` and **1.54 µs/call**, while the 8-tag script stays io-coordinated at
27.40 µs/call `[lab Q5][S1]`.

## Why a synchronous `redis.call` costs a hop

- `CallFromScript` flushes the async buffer with `force = !ca.async`, i.e. a synchronous call always flushes
  immediately `[df-src main_service.cc:2064 CallFromScript][S2]` — Lua needs the reply before the next
  statement, so the batch cannot grow.
- Async calls are buffered instead (`info->async_cmds.emplace_back(...)`) and the flush routes the buffer
  through `MultiCommandSquasher::Execute` `[df-src main_service.cc:2034][S2]`. The buffer also flushes on a
  heap-size limit `[df-src main_service.cc:2019][S2]`.
- `redis.acall` / `redis.apcall` are registered Dragonfly-only Lua functions
  `[df-src interpreter.cc:677-683][S4]`. Use them where the reply is ignored.
- `--lua_auto_async` performs the rewrite statically, **only for atomic scripts**
  `[df-src script_mgr.cc:290][S3]`; for non-atomic ones squashing would lengthen lock hold time.
- Measured: 256 discarded `HSET` over 8 keys/1 tag goes 28.05 -> 2.93 µs/call with `--lua_auto_async=true`
  (throwaway node), while every read variant is unchanged `[lab Q5][S1]`. On the batched Q2 write workload,
  `redis.acall` gives p50 339 µs vs 398 µs for sync `redis.call` on default flags and 108 vs 110 µs under
  `--lock_on_hashtags` (back-to-back arms) `[lab Q10][S1]`.

## Squashing

- What it does: "identifying consecutive series of single-shard commands and separating them by shards,
  while maintaining their relative order within each shard … no further hops are required" `[S5]`.
- `TrySquash` refuses non-transactional, `BLOCKING` and `GLOBAL_TRANS` commands, plus `CLIENT`, keyless and
  multi-shard commands `[df-src multi_command_squasher.cc:TrySquash][S2]`. A non-squashable command flushes
  the batch and then runs standalone `[df-src Run][S2]`.
- Batch size: the struct default is 32 but `--max_squashed_cmd_num` (100) wins at runtime `[df-src][S2]`.
- MULTI/EXEC squashing is skipped when the block runs a script or client tracking is on
  `[df-src main_service.cc:2503 Service::Exec][S2]`.
- Squashing is **not free CPU**: the maintainer's own analysis of pipeline squashing puts `SquashPipeline` at
  ~20% CPU in pprof `[df-doc issue #6006][S12]`. This is why a shard-local script can beat a squashed
  pipeline — measured at 64 `GET` in one hashtag: script p50 186 µs vs pipeline 446 µs under
  `--lock_on_hashtags`, and the reverse on default flags (1860 vs 480 µs) `[lab Q11a][S1]`.

## Interpreters, and the absence of a kill switch

- `--interpreter_per_thread` (default 10) interpreters exist per thread; when they are all busy further
  script calls **block** `[df-src interpreter.cc:1249 InterpreterManager::Get][S4]`. Watch
  `blocked_on_interpreter` (current) and `lua_blocked_total` (cumulative) `[S20]`.
- `SCRIPT LOAD` borrows one of those interpreters and, on v1.34.0, holds it across a journaling hop; the
  contended mean measured by the fix was 620.78 ms before / 259.90 ms after `[df-doc PR #8300][S11]`. Load
  scripts once per process, not per worker boot.
- There is no script timeout, no `SCRIPT KILL`, no BUSY reply and no instruction hook: an infinite script
  pins its thread permanently and recovery is `kill -9` `[df-doc issue #8269][S10]`. Probed absent on
  v1.34.0 `[lab Q9][S1]`.
- Head-of-line blocking is a thread-count question on default flags: 8 loader processes running a ~2 ms
  script, a 9th client's `GET` p99 is 33219 µs at `--proactor_threads=1` and 169 µs at
  `--proactor_threads=4`, and the probe's hashtag makes no difference (33084 / 191 µs for a different tag)
  `[lab Q8][S1]` — consistent with the hashtag not pinning placement on default flags.

## Counters worth reading

`INFO ALL` `[df-src server_family.cc][S2]`; note the Dragonfly INFO reference does not document these
fields `[df-doc][S20]`:

| field | meaning |
|---|---|
| `eval_shardlocal_coordination_total` | scripts that took the single-shard fast path. Want this to dominate. |
| `eval_io_coordination_total` | scripts that paid per-call hops. The ratio is the primary scripting health metric. |
| `eval_squashed_flushes` | times the `acall` buffer was flushed. |
| `blocked_on_interpreter` / `lua_blocked_total` | interpreter-pool starvation, now / cumulative. |
| `tx_shard_polls` | a good proxy for hop volume: Q7's per-candidate variant showed +663192 over 200 invocations vs +49792 for the batched one `[lab Q7][S1]`. |
| `lua_interpreter_cnt`, `lua_interpreter_return`, `used_memory_lua` | pool size, returns, Lua heap. |

Counter caveat: a delta measured while other traffic runs is server-wide. Q11(d)'s `SCRIPT LOAD` arm
reports `eval_*` and `lua_interpreter_return` deltas although it executes no script at all, because 4 loader
processes shared the server — no execution path can be inferred from a delta taken under load `[lab Q11][S1]`.
