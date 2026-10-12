# Server flags and script directives that change script behavior (v1.34.0)

Only the flags that change how a script *executes*. Defaults are the v1.34.0 **source** defaults; where the
docs page disagrees, the conflict is listed at the bottom and the source wins. `dragonfly --helpfull` is the
ground truth for a given binary — Q9 enumerated 20 lua/script/lock/squash/interpreter flags that way
`[lab Q9][S1]`.

## Script directives (`--!df flags=...`)

Dragonfly's directive is `--!df flags=...` on the first line, followed by whitespace
`[df-src script_mgr.cc][S3]`. Redis's `#!lua flags=...` is **not ignored — it is a Lua syntax error**
(`user_script:2: unexpected symbol near '#'`), so a script copied from Redis fails to load `[lab Q9][S1]`.

| directive flag | effect | measured cost |
|---|---|---|
| `disable-atomicity` | mode `NON_ATOMIC`: each command is its own transaction; loses the single-shard fast path `[df-src DetermineMultiMode, CanRunSingleShardMulti][S2]` | under `--lock_on_hashtags`, 8 keys/1 tag, 256 `HGET`, 2048B, n=200: 1.5 -> 30.3 µs/call. On default flags the script was already io-coordinated, so the counter flips without a latency change `[lab Q5][S1]` |
| `allow-undeclared-keys` | mode `GLOBAL`: shard-level lock on **every** shard for the script's duration `[S5][S7]` | same shape: 1.5 -> 30.1 µs/call under `--lock_on_hashtags` `[lab Q5][S1]`. The docs put it plainly: it "locks the entire data store for each Lua script execution and slows things down considerably" `[df-doc][S7]` |
| `no-writes` | parsed for Redis compatibility, **no-op** `[df-src script_mgr.cc][S3]` | none — it does not enable `EVAL_RO`-style guarantees here |

Any other flag name is a hard load error `[df-src script_mgr.cc][S3]`, including every Redis flag
(`allow-oom`, `allow-stale`, `no-cluster`, `allow-cross-slot-keys`) `[S22]`.

Three ways to set them: in the script, `--default_lua_flags=...` server-wide, or
`SCRIPT FLAGS <sha> <flag>` — which **can be called before the script is loaded**, the supported way to
patch a framework script you cannot edit; find SHAs with `SCRIPT LIST` `[df-doc][S7][lab Q9][S1]`.
`SCRIPT FLAGS <sha>` with no flag argument errors: it is a setter, not a getter `[lab Q9][S1]`.

## Placement and locking

| flag | v1.34.0 default | why it matters |
|---|---|---|
| `--lock_on_hashtags` | `false` | Locks and places by `{tag}` rather than by key. **This is the only thing that makes a shared hashtag co-locate** `[lab Q5][S1][df-src lock_tags][S2][S30]`. See the trade-off below. |
| `--locktag_delimiter`, `--locktag_prefix`, `--locktag_skip_n_end_delimiters` | `""`, `""`, `0` | Generalize the tag beyond `{...}` (e.g. delimiter `:` with skip 2 makes `:a:b:c:d:e` a tag of `a:b:c`); require `--lock_on_hashtags` `[df-src common.cc][S8]` |
| `--num_shards` / `--proactor_threads` | `0` (auto) | The shard count decides whether your declared keys land on one shard at all `[df-src main_service.cc][S2]` |
| `--migrate_connections` | `true` | Enables the fast path's thread migration; Lua-only, at most once per connection `[df-doc][S8]` |

**The `--lock_on_hashtags` trade-off, measured.** It converts a one-hashtag multi-key script from
io-coordinated to shard-local: 256 `HGET` over 8 keys in one tag, 2048B, atomic, n=200 goes 26.95 -> 1.54
µs/call and the counters flip from `eval_io_coordination_total` to `eval_shardlocal_coordination_total`
`[lab Q5][S1]`. Back to back on three real workloads it shrinks the remaining wins (batching 9.9x -> 2.0x,
payload skip 1.3x -> 1.8x, `claim_mailbox_batch` 7.6x -> 2.6x) because the baseline got much faster
`[lab Q10][S1]`.

It pays for that by pinning every key with a given tag to one thread. Under 4 concurrent loader processes
running a ~2 ms script on the shared tag, `EVALSHA` of a **trivial 1-key script that touches none of the
loader's keys** costs p50 7712 µs on the `--lock_on_hashtags` node against 81 µs on the default node, while
both serve it in 115-125 µs idle; a fresh-text `SCRIPT LOAD` in the same cell goes 440 -> 7787 µs
`[lab Q11d][S1]`. Q8's reassuring `--proactor_threads=4` head-of-line result was measured on default flags
only and does not cover this `[lab Q8][S1]`.

**Decision rule.** Consider the flag only if the number of distinct tags is at least the shard count, or the
workload's total script CPU time fits one core. Then measure both arms at the workload's real concurrency,
watching client p99, not p50. Skew is the failure mode: a hashtag is a heat unit `[df-doc][S15]`. BullMQ's
integration docs do require `--cluster_mode=emulated --lock_on_hashtags`, and the vendor measured 2.26x from
the application-side hashtag change `[df-doc][S19]`.

## Interpreters, async and squashing

| flag | v1.34.0 default | why it matters |
|---|---|---|
| `--interpreter_per_thread` | `10` | When all are busy on a thread, further script calls **block** `[df-src interpreter.cc:1249][S4]`. Rising `lua_blocked_total` means raise it or shorten scripts. `SCRIPT LOAD` borrows one too `[df-doc PR #8300][S11]` |
| `--lua_auto_async` | `false` | Statically rewrites discarded-result `call`/`pcall` into `acall`/`apcall`, **atomic scripts only** `[df-src script_mgr.cc:290][S3]`. Measured: 256 discarded `HSET`, 8 keys/1 tag, 28.05 -> 2.93 µs/call; reads unchanged `[lab Q5][S1]` |
| `--multi_exec_squash` | `true` | Squashes single-shard commands in MULTI/EXEC — disabled when the block runs a script `[df-src Service::Exec][S2]` |
| `--max_squashed_cmd_num` | `100` | Commands squashed per shard per hop; overrides the struct's 32 at runtime `[df-src][S2]` |
| `--multi_eval_squash_buffer` | `4096` | Buffered squashed-command memory per script; the buffer also flushes on this limit `[df-src main_service.cc:2019][S2]` |
| `--max_busy_squash_usec` | `1000` | Busy budget during squashed execution before yielding `[df-src multi_command_squasher.cc][S2]` |
| `--pipeline_squash` | `1` | Client-pipeline squashing threshold — **unrelated to MULTI/Lua**, and the CPU cost that makes "pipeline instead of script" a bad default `[df-doc issue #6006][S12]` |

## Lua runtime and compatibility

| flag | v1.34.0 default | why it matters |
|---|---|---|
| `--default_lua_flags` | `""` | Applies `allow-undeclared-keys` / `disable-atomicity` to **every** script; both kill the fast path `[df-src script_mgr.cc][S3][lab Q5][S1]` |
| `--lua_enable_redis_log` | `false` | `redis.log` writes nothing unless this is on (and the function only exists from v1.33.0) `[df-src interpreter.cc][S4]` |
| `--lua_undeclared_keys_shas` | **4 built-in SHAs** (3 Sidekiq, 1 Sentry) | Read **only at script load time**; changing it does not affect already-loaded scripts `[df-src script_mgr.cc][S3]` |
| `--lua_force_atomicity_shas` | 1 built-in SHA (Sidekiq) | Forces atomicity even if the script declares `disable-atomicity`; undocumented on the site `[df-src script_mgr.cc][S3]` |
| `--lua_allow_undeclared_auto_correct` | `false` | On an undeclared-key error, sets the script's flag so it can rerun — i.e. silently moves it to the GLOBAL path `[df-src script_mgr.cc][S3]` |
| `--lua_mem_gc_threshold` | `10000000` | Per-thread Lua bytes before forced GC; 0 disables `[df-src interpreter.cc][S4]` |
| `--lua_resp2_legacy_float` | `false` | Returns truncated ints instead of floats from Lua `[df-src main_service.cc][S2]` |

Memory flags that bite scripts: `--maxmemory` must cover **two copies** of a large value you overwrite — a
32 MiB `cjson` blob wrote once and then failed with `-ERR Out of memory` at `--maxmemory=2048Mi`
`[lab Q4][S1]`. `--cache_mode` (default `false`) decides whether pressure evicts or errors.

Retired at v1.34.0, do not recommend: `--multi_exec_mode`, `--track_exec_frequencies` `[df-src][S8]`.

## Docs-vs-source conflicts at v1.34.0 (trust the source)

- `--multi_eval_squash_buffer`: docs say **8096**, v1.34.0 source says **4096** `[S8]`.
- `--lua_undeclared_keys_shas`: docs show an **empty** default; v1.34.0 ships 4 hardcoded SHAs `[S3][S8]`.
- `--lock_on_hashtags`: the docs add "Only use this with `--cluster_mode=emulated|yes`"; the v1.34.0 source
  comment does not say this (it says the mechanism is generalized tags, not hashtags) `[S8][df-src common.cc]`.
- `--lua_float_as_int_shas` is on the docs page but **does not exist at v1.34.0** — it is newer `[S8]`.
- The `eval_*` / `lua_*` INFO counters this skill relies on are **undocumented** in the INFO reference and
  not exported by Prometheus (except `lua_interpreter_cnt`, `lua_blocked_total`) `[df-doc][S20][df-src server_family.cc][S2]`.
- `docs/transaction.md` on `main` is a post-v1.34.0 rewrite; the shipped v1.34.0 copy leaves the
  optimization section as `TBD` `[S5]`.
