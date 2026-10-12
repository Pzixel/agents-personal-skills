# Dragonfly Lua/scripting research sources

**Target: v1.34.0** — tag `v1.34.0`, commit `65acd60eb3288684adc2ddf287de34a807812df4`, published **2025-09-17**.
Release title is literally `v1.34.0 (Use with caution - see known issues in description)` (known issues #5899, #5891).
Newest release as of 2026-09-16 is v1.40.2; v1.34.2 (2025-10-14) advises upgrading due to a **cache-mode** regression.

Citation convention: `[SRC]` = read from a local clone of tag v1.34.0 (authoritative for v1.34.0).
`[DOC <date>]` = dragonflydb.io, which is **unversioned** (pages read "Last updated on Aug 4, 2026") and may describe a
newer build — every docs/source conflict found is called out explicitly. `[MAIN]` = repo `main`, newer than v1.34.0.

## Top facts that change how to write scripts

1. **The shebang is `--!df flags=...`, NOT Redis's `#!lua flags=...`.** Must be on the first line, followed by whitespace. A `#!lua` line does nothing. [SRC `script_mgr.cc` regex]
2. **Only three flags parse: `disable-atomicity`, `allow-undeclared-keys`, `no-writes`.** `no-writes` is accepted for Redis compat but is a **no-op**. Any other flag is a hard error. [SRC]
3. **If every declared key hashes to one shard, Dragonfly runs the entire script on that shard thread** — zero per-`redis.call` hops, and the connection may migrate to that thread. This is the single biggest scripting win. [SRC `EvalInternal`]
4. **That fast path requires the default mode (atomic + declared keys).** Both `disable-atomicity` and `allow-undeclared-keys` disable it. Optimizing by adding flags usually makes scripts slower. [SRC `CanRunSingleShardMulti`]
5. **`allow-undeclared-keys` promotes the script to a GLOBAL transaction that stops the entire server** for its duration, across all shards, even for unrelated keys. [DOC + SRC]
6. Declare every key in `KEYS`. Undeclared access fails with `script tried accessing undeclared key, key: <k>`. [SRC `facade/error.h`]
7. **Co-locate keys with a hashtag `{...}` so they land on one shard.** With `--lock_on_hashtags`, locking is per-tag, so a script can declare just the tag. BullMQ measured 2.26x from this application-side change alone. [BLOG]
8. **Each synchronous `redis.call` costs one coordinator↔shard round trip ("hop")** on the multi-shard path, because Lua needs the reply immediately and the batch must be flushed. [SRC `CallFromScript`]
9. **`redis.acall` / `redis.apcall` are Dragonfly-only async variants**: their commands are buffered and executed as one squashed hop. Use them for calls whose return value you ignore. [SRC `interpreter.cc`]
10. `--lua_auto_async` (default **false**) does this rewrite automatically, but **only for atomic scripts** — for non-atomic ones squashing would lengthen lock hold time. [SRC]
11. **Squashing only batches consecutive single-shard, non-blocking, keyed commands.** One multi-shard, keyless, BLOCKING or GLOBAL command flushes the batch and runs standalone. [SRC `TrySquash`]
12. **Measure the path**: `INFO` exposes `eval_shardlocal_coordination_total` (fast) vs `eval_io_coordination_total` (hops). Their ratio is the primary scripting health metric. [SRC]
13. **Long scripts starve the interpreter pool**: only `--interpreter_per_thread` (default 10) run per thread; the rest block. Watch `lua_blocked_total` / `blocked_on_interpreter`. [SRC]
14. **No script timeout, no `SCRIPT KILL`, no BUSY reply, no instruction hook.** An infinite script *pins its thread permanently*; every client on that thread hangs and recovery is `kill -9`. Bound the work yourself. [issue #8269, open]
15. **Redis Functions do not exist**: `FCALL` and all `FUNCTION *` are Unsupported. Use EVAL/EVALSHA. [DOC compatibility]
16. **`redis.setresp` does not exist** in v1.34.0 (not registered on the `redis` table). `redis.replicate_commands` exists only as a **no-op** — Dragonfly replicates script *effects*.
17. **`redis.log` is silent unless `--lua_enable_redis_log=true`**, and it only exists from v1.33.0 onward.
18. Lua is **5.4.4**, not Redis's 5.1: integers are a real type. Libraries: base, table, string, math, `cjson`, `struct`, `cmsgpack`, `bit`. No `os`, `io`, `package`; `debug` is set to `nil`.
19. **`SCRIPT FLAGS <sha> <flags>` can be called *before* a script is loaded** — the supported way to patch framework scripts you cannot edit. Find SHAs with `SCRIPT LIST`.
20. `--lua_undeclared_keys_shas` ships with **4 hardcoded SHAs** (3 Sidekiq, 1 Sentry) and is **read only at script load time**; changing it does not affect already-loaded scripts.

---

## 1. Scripting docs — https://www.dragonflydb.io/docs/managing-dragonfly/scripting

Page sections are only: "Script flags", "Allowing undeclared keys", "Sandbox restrictions". It does **not** document
`disable-atomicity`, `--lua_undeclared_keys_shas`, `--lock_on_hashtags`, global vs hashtag locking, or multi-shard vs
single-shard execution. Those come from the flags page and from source (sections 2 and 5).

- Shebang syntax || [DOC 2026-09-16] "--!df flags=allow-undeclared-keys" — confirmed [SRC `script_mgr.cc:240` `DeduceParams`]: `static const regex kRegex{R"(^\s*?--!df flags=([^\s\n\r]*)[\s\n\r])"};` — first line only, trailing whitespace required.
- No flags are set by default || [DOC] "Dragonfly provides additional flexibility with special script flags. By default, none are set."
- Three ways to set flags: in source, `--default_lua_flags`, or `SCRIPT FLAGS` || [DOC] "./dragonfly --default_lua_flags=allow-undeclared-keys" / "SCRIPT FLAGS sha1 allow-undeclared-keys"
- `SCRIPT FLAGS` works before the script is loaded — patch framework scripts || [DOC] "This command can be called even before the script is loaded. This makes it possible to patch scripts used by frameworks or side applications."
- Discover framework SHAs with `SCRIPT LIST` first || [DOC] "First, determine what SHAs are used by the framework/application. This can be done with the SCRIPT LIST command."
- Undeclared keys forbidden by default || [DOC] "Dragonfly forbids accessing undeclared keys from scripts and returns the following error: script tried accessing undeclared key"
- **Documented cost of undeclared keys** || [DOC] "This option is disabled by default because unpredictability, atomicity and multithreading don't mix well. If enabled, Dragonfly has to stop all other operations when the script is running."
- Lua version || [DOC] "Dragonfly uses Lua version 5.4." / [SRC `docs/differences.md`] "We use lua 5.4.4 that has been released in 2022. That means we also support lua integers"
- Sandbox: `load()` is text-mode only; `rawset`/`setmetatable`/`getmetatable` are replaced by protected versions refusing to touch `_G`'s metatable; `dragonfly.randstr()` validates its size argument || [DOC] — **sandbox hardening may postdate v1.34.0; UNVERIFIED for v1.34.0 specifically.**
- Why squashing is limited to atomic scripts || [SRC `script_mgr.cc:287`] "For non atomic modes, squashing increases the time locks are held, which can decrease throughput with frequently accessed keys."

---

## 2. Transaction model — how a Lua script is scheduled

Primary source: `docs/transaction.md`, shipped in v1.34.0 as "Life of a transaction" (256 lines).
**Caveat:** the `main` version ("Dragonfly Transaction Model", 363 lines) is a **post-v1.34.0 rewrite**. The shipped
v1.34.0 doc leaves the key section unwritten — literally `## Optimizations / Out of order transactions - TBD`.
For v1.34.0 behavior the code is authoritative.

### Hops and scheduling
- A **hop** = one blocking coordinator→shard message round trip || [SRC] "the coordinator sends messages to the shards... Every time, the coordinator sends a message, it blocks until it gets an answer. We call such interaction a *message hop*"
- Two phases: scheduling, then execution of one or more hops || [SRC] "The flow consists of two different phases: *scheduling* a transaction, and *executing* it."
- Only the coordinator **fiber** blocks, not the thread || [SRC] "Note, that only the coordinator fiber is blocked. Its thread can still execute other fibers"
- Coordinator waits for **all** shards before the next hop, and may use intermediate replies || [SRC] "Only when all shards executed the micro-op and return the result, the coordinator is unblocked"
- Worked example of hop counts || [SRC] "'MSET' corresponds to a single micro-op... However, 'RENAME' requires two micro-ops"
- Ordering uses a **global atomic sequence counter** (VLL-based) || [SRC] "Dragonfly maintains a global sequence counter that is used to induce a total order for all its transactions." + "This counter may be a source of contention - it breaks the shared nothing model, after all."
- Scheduling may fail and retry with a new id; after scheduling there is no rollback || [SRC] "it fails the scheduling request" / "it never rollbacks, or retries. Once it's been scheduled, VLL guarantees the progress"
- Locks are **intent** locks — a `key→counter` map, not blocking mutexes || [SRC] "Those locks do not block the flow of the underlying operation but merely express the intent to touch or modify the key. In reality, they are represented by a map: `lock:str->counter`"
- Scheduled ≠ exclusive || [SRC] "a scheduled transaction does not hold exclusivity on its keys."
- Algorithm is VLL-derived || [SRC] "The algorithm behind Dragonfly transactions is based on the [VLL paper]"
- Shared-nothing basics || [MAIN `docs/df-share-nothing.md`] "Each database shard is owned and accessed by a single thread." / "Inter-thread interactions in Dragonfly occur only via passing messages from thread to thread."
- SHARED (reads) vs EXCLUSIVE (writes) intent counters; lock table keyed by 64-bit `LockFp` fingerprints of `LockTag`; collisions cost performance, never correctness || [MAIN `docs/transaction.md`] — documented post-v1.34.0 but describes v1.34.0 code.

### The three multi-modes (MULTI/EXEC and Lua)
- Scripts are modelled as consecutive commands in ONE multi-transaction || [SRC] "commands produced by Lua scripts are modelled as consecutive commands within a Dragonfly transaction."
- **GLOBAL** — required for undeclared keys; serializes the whole server || [SRC] "This mode is required for global commands (like MOVE) and for accessing undeclared keys in Lua scripts. Otherwise, it should be avoided, because it prevents Dragonfly from running concurrently and thus greatly decreases throughput."
- Global takes the **shard-level** lock on every shard || [MAIN] "A global transaction acquires the shard-level lock (not individual key locks) on every shard, preventing any other transaction from executing until it completes."
- **LOCK_AHEAD** — the default; scheduled on exactly the declared keys || [SRC] "It is scheduled on all keys used by the commands in the transaction block, or Lua script"
- **NON_ATOMIC** — each command is its own transaction || [SRC] "All commands are executed as separate transactions making the multi-transaction not atomic. It vastly improves the throughput with contended keys, as locks are acquired only for single commands."

### EVAL code path (decisive facts)
- Mode is derived purely from the two flags || [SRC `main_service.cc` `DetermineMultiMode`] "if (params.atomic && params.undeclared_keys) return Transaction::GLOBAL; else if (params.atomic) return Transaction::LOCK_AHEAD; else return Transaction::NON_ATOMIC;"
- **Single-shard fast path** runs the script on the shard thread || [SRC `EvalInternal`] "if (CanRunSingleShardMulti(sid, *params, *tx)) { // If script runs on a single shard, we run it remotely to save hops."
- It requires LOCK_AHEAD, i.e. is killed by either flag || [SRC `CanRunSingleShardMulti`] "if (DetermineMultiMode(params) != Transaction::LOCK_AHEAD) { return false; }"
- Shard id comes from declared KEYS only; one differing key collapses it || [SRC] "ShardId cur_sid = Shard(key, shard_count()); ... if (sid.has_value() && *sid != cur_sid) { sid = nullopt; }"
- The connection may be migrated to the shard's thread || [SRC] "cntx->conn()->RequestAsyncMigration(shard_set->pool()->at(*sid), false);" — enabled by `--migrate_connections` (default true), which [DOC] says is "only supported for Lua script invocations, and can happen at most once per connection."
- Keys are locked by **LockTag**, not raw key — the hook `--lock_on_hashtags` uses || [SRC] "sinfo->lock_tags.insert(LockTag(key));"
- Undeclared-key check compares LockTags || [SRC `main_service.cc:1198` + `facade/error.h:37`] `inline constexpr char kUndeclaredKeyErr[] = "script tried accessing undeclared key";`
- `EVAL_RO` rejects writes at call time || [SRC:1358] "if (dfly_cntx.conn_state.script_info->read_only && is_write_cmd) {"
- EVAL cannot be nested || [SRC] "DCHECK(!cntx->conn_state.script_info);  // we should not call eval from the script."
- SHA must be exactly 40 hex chars || [SRC] "if (eval_args.sha.size() != 40 || !IsSHA(eval_args.sha))"
- A concluding, non-global, **single-shard** transaction executes optimistically inline and **skips the global counter** || [SRC `transaction.cc:738` `ScheduleInternal`] "bool optimistic_exec = !IsGlobal() && (coordinator_state_ & COORD_CONCLUDING) && (unique_shard_cnt_ == 1 || (cid_->opt_mask() & CO::IDEMPOTENT));" and ":750" "// This is a contention point for all threads - avoid using it unless necessary." / "if (unique_shard_cnt_ > 1) txid_ = op_seq.fetch_add(1, memory_order_relaxed);"

### Squashing and async calls
- The two stated costs of a command sequence || [SRC `docs/transaction.md`] "each command invocation requires an expensive hop" / "executing commands sequentially makes no use of our multi-threaded architecture"
- What squashing does || [SRC] "identifying consecutive series of single-shard commands and separating them by shards, while maintaing their relative order withing each shard" / "Because all commands are already placed on their relevant threads, no further hops are required and all command callbacks are executed inline."
- **A synchronous `redis.call` forces an immediate flush → one hop** || [SRC `main_service.cc:2064` `CallFromScript`] "if (auto err = FlushEvalAsyncCmds(cntx, !ca.async || findcmd_err.has_value()); err) {" — force is true whenever `!ca.async`.
- Async calls are buffered instead || [SRC:2058] "info->async_cmds.emplace_back(std::move(*ca.buffer), cid, ca.args.subspan(1), replies);"
- The flush routes the buffer through the squasher || [SRC:2034] "MultiCommandSquasher::Execute(absl::MakeSpan(info->async_cmds), &crb, cntx, this, opts);"
- Buffer also flushes on size || [SRC:2019] "if ((info->async_cmds.empty() || !force) && used_mem < info->async_cmds_heap_limit) return nullopt;"
- `redis.acall`/`redis.apcall` are registered Lua functions || [SRC `interpreter.cc:677-683`] "/* redis.acall */ lua_pushstring(lua_, "acall");"
- `--lua_auto_async` only applies to atomic scripts || [SRC `script_mgr.cc:290`] "if (params.atomic && absl::GetFlag(FLAGS_lua_auto_async)) {" — static detector `Interpreter::DetectPossibleAsyncCalls`.
- On the single-shard fast path squashing is explicitly disabled || [SRC `EvalInternal`] "// Disable squashing, as we're using the squashing mechanism to run remotely. args.async = false;"
- MULTI/EXEC squashing is disabled when the block runs a script or client tracking is on || [SRC `main_service.cc:2503` `Service::Exec`] "if (GetFlag(FLAGS_multi_exec_squash) && state != ExecScriptUse::SCRIPT_RUN && !cntx->conn_state.tracking_info_.IsTrackingOn()) {"
- Per-command squash disqualifiers || [SRC `multi_command_squasher.cc` `TrySquash`] "if (!cmd->Cid()->IsTransactional() || (cmd->Cid()->opt_mask() & CO::BLOCKING) || (cmd->Cid()->opt_mask() & CO::GLOBAL_TRANS)) return SquashResult::NOT_SQUASHED;" — also `CLIENT`, keyless commands, and multi-shard commands.
- A non-squashable command flushes the batch, then runs standalone || [SRC `Run`] "if (!ExecuteSquashed(rb)) break;  // if the last command was not added - we squash it separately."
- Atomic vs non-atomic squash dispatch differ || [SRC `multi_command_squasher.h` class comment] "Non atomic multi transactions use regular shard_set dispatches instead of hops for executing batches. This allows avoiding locking many keys at once."
- `Opts::max_squash_size` defaults to 32 in the struct but `--max_squashed_cmd_num` (100) wins at runtime || [SRC] "unsigned max_squash_size = 32;  // How many commands to squash at once"

---

## 3. BullMQ blog posts — the canonical worked example

- Part 1 (2023-10-16) announces compatibility || https://www.dragonflydb.io/blog/running-bullmq-with-dragonfly-part-1-announcement "we are excited to announce that Dragonfly is now fully compatible with BullMQ."
- Root cause of slowness || part-2 "Dragonfly's mechanism involves locking all declared keys **prior** to executing a Lua script"
- Hops named as the latency source || part-2 "Dragonfly has to schedule the work between their respective threads (we call these "hops"), which adds significant latency."
- Global locks block unrelated keys || part-2 "No other commands or scripts can run alongside a global transaction, even if they involve completely different keys."
- Hashtag locking lets a script declare only the queue name || part-2 "Dragonfly locks based on the hashtag rather than the entire key." / "by not specifying the exact keys which will be used, but only the queue name itself."

**Optimization ladder** (add-jobs/sec, AWS c7i, 8 queues / 16 worker threads, 2023-11-21). Redis baseline **71,351**.
https://www.dragonflydb.io/blog/running-bullmq-with-dragonfly-part-2-optimization

| Step | ops/sec | vs baseline |
|---|---|---|
| Dragonfly with global locks | 7,697 | 1.00x (~9x **slower** than Redis) |
| + Hashtag locks | 17,403 | 2.26x |
| + Reduced hops for commands | 53,011 | 6.98x |
| + Reduced hops for scripts | 122,890 | 15.97x |
| + Connection migration | 189,756 | 24.65x |
| + Shard round robin | 253,075 | 32.87x (~3.5x Redis) |

Read this as: **hashtags are the application-side lever (2.26x)**; the rest were server-side changes that already ship in
v1.34.0 — the two largest being "reduced hops for scripts" and "connection migration", i.e. the single-shard fast path.

- Required server flags || https://www.dragonflydb.io/docs/integrations/bullmq "./dragonfly --cluster_mode=emulated --lock_on_hashtags"
- App must add a hashtag, use **distinct** tags per queue, but **share** a tag across parent/child queues || [DOC] "const queue = new Queue("{myqueue}");" / "use unique per-queue prefixes so that not all queues are handled by the same Dragonfly thread" / "If you have queue dependencies, especially a parent-child relationship, it's important to use the same hashtag for both queues."
- The fallback and its cost || [DOC] "running Dragonfly with --default_lua_flags=allow-undeclared-keys locks the entire data store for each Lua script execution and slows things down considerably."
- **`--shard_round_robin_prefix` — the blog's final 32.87x step — is DEPRECATED in v1.34.0 and logs a warning.** Do not recommend it. || [SRC `sharding.cc:18,37`] `ABSL_FLAG(string, shard_round_robin_prefix, "", "Deprecated -- will be removed");` / `LOG(WARNING) << "shard_round_robin_prefix is deprecated and will be removed in new versions";` — the docs flags page still lists it without a deprecation notice.
- The Dragonfly Lua blog post (/blog/leveraging-power-of-lua-scripting) covers only `allow-undeclared-keys` / `disable-atomicity` — **no** mention of hops, `acall`, or squashing.

---

## 4. Command reference

**There is no "Scripting" category.** Pages live under `/docs/command-reference/generic/`; `/command-reference/scripting/*` 404s.
Exist (200): `eval`, `eval-ro`, `evalsha`, `evalsha-ro`, `script`, `script-load`, `script-exists`, `script-help`, `script-latency`, `script-list`.
**404 and absent from the sitemap:** `script-flush`, `script-flags`, `script-gc`, `script-kill`. So `SCRIPT FLAGS` — the command
the scripting guide tells you to use — has **no reference page**.

- EVAL syntax + the core rule || [DOC] "EVAL script numkeys [key [key ...]] [arg [arg ...]]" / "all names of keys that a script accesses must be explicitly provided as input key arguments."
- EVAL_RO semantics || [DOC] "Scripts executed via EVAL_RO are treated as read-only and may run with different transaction semantics." / error "Write commands are not allowed from read-only scripts"
- **`SCRIPT LATENCY` output** || [DOC] "Prints latency histograms in usec for all called scripts." / "The first element is the SHA1 digest... The second element is latency historgram." [sic] — [SRC `ScriptMgr::LatencyCmd`] returns an array of `[sha, verbatim-string]` pairs where the string is a `base::Histogram` merged across **all threads**: "rb->StartArray(2); rb->SendBulkString(k_v.first); rb->SendVerbatimString(k_v.second.ToString());" — **bucket boundaries are internal to `base::Histogram` in the helio dependency: UNVERIFIED.** Treat the string as opaque; diff it across runs.
- `SCRIPT LIST` returns `[sha1, script body]` pairs (Dragonfly-only; Redis has no SCRIPT LIST) || [DOC] "The first element is the SHA1 digest of the scripts added into the script cache. The second element is lua script."
- **`SCRIPT FLAGS <sha> <flag> [flag...]`** requires ≥1 flag and replies `+OK` || [SRC `ScriptMgr::ConfigCmd` + HELP text] "FLAGS <sha> [flags ...]", "   Set specific flags for script. Can be called before the sript is loaded."
- `SCRIPT GC` forces Lua GC on every interpreter on every thread (Dragonfly-only, undocumented on the site) || [SRC `GCCmd`] "auto cb = [](Interpreter* ir) { ir->RunGC(); ThisFiber::Yield(); };"
- Supported subcommands: HELP, EXISTS, LOAD, FLUSH, LIST, LATENCY, FLAGS, GC. **No KILL, no DEBUG.** || [SRC `ScriptMgr::Run`]
- Compatibility table || https://www.dragonflydb.io/docs/command-reference/compatibility "SCRIPT FLUSH | Partially supported | Missing: ASYNC, SYNC." / "SCRIPT KILL | Unsupported" / "FCALL | Unsupported" / "FUNCTION * | Unsupported"
- Flag separators are comma, semicolon **or** space || [SRC `ApplyFlags`] "absl::StrSplit(config, absl::ByAnyChar(",; "), absl::SkipEmpty())"
- Defaults asserted statically as atomic + declared-only || [SRC] "static_assert(ScriptParams{}.atomic && !ScriptParams{}.undeclared_keys);"

---

## 5. Server config flags

Defaults below are the literal `ABSL_FLAG` defaults **read from the v1.34.0 tree** — authoritative for v1.34.0.
The docs page is a curated subset; it says so: "You can try dragonfly --helpfull to get a list of all flags".

| Flag | v1.34.0 default | Meaning | Defined in |
|---|---|---|---|
| `--default_lua_flags` | `""` | Default flags for all scripts (`allow-undeclared-keys`, `disable-atomicity`) | `server/script_mgr.cc` |
| `--lua_auto_async` | `false` | Rewrite discarded-value `call`/`pcall` into `acall`/`apcall` (atomic scripts only) | `server/script_mgr.cc` |
| `--lua_allow_undeclared_auto_correct` | `false` | On undeclared-key error, auto-set the script's flag so it can rerun | `server/script_mgr.cc` |
| `--lua_undeclared_keys_shas` | **4 built-in SHAs** (3 Sidekiq, 1 Sentry) | SHAs allowed undeclared keys; **read only at load time** | `server/script_mgr.cc` |
| `--lua_force_atomicity_shas` | 1 built-in SHA (Sidekiq) | Force atomic even if the script says `disable-atomicity`; **undocumented on the site** | `server/script_mgr.cc` |
| `--lua_mem_gc_threshold` | `10000000` | Per-thread Lua bytes after which GC is forced; 0 disables forced GC | `core/interpreter.cc` |
| `--luagc` | unset | Lua GC tuning, `inc/200/100/13` or `gen/20/100` | `core/interpreter.cc` |
| `--lua_enable_redis_log` | `false` | **`redis.log` writes nothing unless on** | `core/interpreter.cc` |
| `--lua_resp2_legacy_float` | `false` | Return truncated ints instead of floats from Lua | `server/main_service.cc` |
| `--interpreter_per_thread` | `10` | Lua interpreters per thread; exceeding it **blocks** callers | `server/server_state.cc` |
| `--multi_exec_squash` | `true` | Squash single-shard commands in MULTI/EXEC | `server/main_service.cc` |
| `--multi_eval_squash_buffer` | **`4096`** | Max buffered squashed-command memory per script | `server/main_service.cc` |
| `--max_squashed_cmd_num` | `100` | Max commands squashed per shard per hop | `server/server_state.cc` |
| `--max_busy_squash_usec` | `1000` | Busy budget (usec) during squashed execution before yielding | `server/multi_command_squasher.cc` |
| `--log_squash_info_threshold_usec` | `1<<31` | Log squashing timings above this | `server/multi_command_squasher.cc` |
| `--squash_stats_latency_lower_limit` | `0` | Skip latency stats below this usec | `server/main_service.cc` |
| `--lock_on_hashtags` | `false` | Lock at `{hashtag}` granularity instead of key granularity | `server/common.cc` |
| `--locktag_delimiter` | `""` | Custom lock-tag delimiter; requires `--lock_on_hashtags` | `server/common.cc` |
| `--locktag_prefix` | `""` | Only keys with this prefix participate in tag extraction | `server/common.cc` |
| `--locktag_skip_n_end_delimiters` | `0` | e.g. delimiter `:` with value 2 makes `:a:b:c:d:e` → tag `a:b:c` | `server/common.cc` |
| `--migrate_connections` | `true` | Enables the fast-path thread migration; Lua-only, at most once per connection | `facade/dragonfly_connection.cc` |
| `--num_shards` / `--proactor_threads` | `0` (auto) | Shard count — determines whether your keys co-locate | `server/main_service.cc` |
| `--pipeline_squash` | `1` | Queued pipelined commands above which squashing is enabled; 0 disables. **Unrelated to MULTI/Lua.** | `facade/dragonfly_connection.cc` |
| `--maxmemory` | `0` (auto) | Eviction threshold; "Must be *at least* 256MiB per proactor thread" | `server/main_service.cc` |
| `--cache_mode` | `false` | Evict entries near maxmemory instead of erroring | `server/engine_shard_set.cc` |
| `--serialization_max_chunk_size` | `65536` (64 KB) | Values above this use streaming serialization in snapshot/full-sync; 0 disables streaming | `server/server_state.cc` |
| `--rss_oom_deny_ratio` | `1.25` | DENYOOM commands fail above this RSS/maxmemory ratio | `server/server_state.cc` |

Snapshot flags (brief, docs-side): `--snapshot_cron` (empty), `--snapshot_egress_limit_bytes` (0B, 0 = no throttling),
`--df_snapshot_format` (true), `--background_snapshotting` (false), `--dbfilename` (`dump-{timestamp}`),
`--serialization_tagged_chunks` (true), `--save_schedule` (deprecated → use `--snapshot_cron`).

**Retired in v1.34.0 — do not recommend:** `--multi_exec_mode`, `--track_exec_frequencies` || [SRC] `ABSL_RETIRED_FLAG(uint32_t, multi_exec_mode, 2, "DEPRECATED. Sets multi exec atomicity mode");`

**Docs/source conflicts (trust the source for v1.34.0):**
- `--multi_eval_squash_buffer`: docs say **8096**, v1.34.0 source says **4096**.
- `--lua_undeclared_keys_shas`: docs show an **empty** default; v1.34.0 ships 4 hardcoded SHAs.
- `--lock_on_hashtags`: docs add "Only use this with --cluster_mode=emulated|yes"; the v1.34.0 source comment does not say this. Source comment: "We've generalized "hashtags"... If I had a time machine, I'd rename this to lock_on_tags."
- `--lua_float_as_int_shas` appears on the docs page but **does not exist at v1.34.0** (grep returns nothing). **Newer than v1.34.0.**

---

## 6. Memory / data-type facts affecting key layout

- Hash listpack→dense conversion is driven by **bytes and field length, not entry count**: `max_listpack_map_bytes` **1024**, `max_map_field_len` **64** || [SRC `redis/redis_aux.c:21-22`, used in `hset_family.cc:49`] "return lpBytes(...) + sum < server.max_listpack_map_bytes;"
- ZSET listpack threshold `zset_max_listpack_entries` = **128** || [SRC `redis/redis_aux.c:18`]
- `--list_max_listpack_size` default **-2** || [SRC `list_family.cc:43`]
- Sets use `kEncodingIntSet` or a Dragonfly-specific **dense set** (design doc `docs/dense_set.md`); hashes use `kEncodingListPack` when small || [SRC `debugcmd.cc` `AddObjHist`]
- `DEBUG OBJHIST` prints a histogram of object sizes — per type it tracks value length, entry length, cardinality and listpack bytes || [SRC `debugcmd.cc`] "OBJHIST" / "    Prints histogram of object sizes."
- `DEBUG POPULATE` full syntax || [SRC `debugcmd.cc` help] "POPULATE <count> [prefix] [size] [RAND] [SLOTS start end] [TYPE type] [ELEMENTS elements] [EXPIRE start end]"
- `MEMORY USAGE <key>` is implemented || [SRC `memory_cmd.cc:151`] `parser.Check("USAGE")`, helper `MemoryUsage(PrimeIterator, bool account_key_memory_usage)`
- Big values hurt snapshots: above `--serialization_max_chunk_size` (64 KB) values switch to streaming serialization || [SRC `server_state.cc:37`] "Values bigger than this threshold will be serialized using streaming serialization."
- Sets convert intset → dense set (`kEncodingStrMap2`) at **256** entries, hardcoded || [SRC `set_family.cc:51`] "constexpr uint32_t kMaxIntSetEntries = 256;" — Redis's `set-max-intset-entries` default is 512 and Redis also has a listpack set encoding; Dragonfly's path is intset-or-dense only.
- Also hardcoded: `zset_max_listpack_value` **32**, `stream_node_max_bytes` **4096**, `stream_node_max_entries` **100** || [SRC `redis/redis_aux.c:19,24,25`]
- **`OBJECT ENCODING` is Unsupported** (as are OBJECT FREQ/IDLETIME/REFCOUNT) — you cannot introspect encoding the Redis way, which is why `DEBUG OBJHIST` matters || [DOC compatibility]
- `DEBUG OBJHIST` output is a RESP **verbatim string** delimited by `___begin object histogram___` / `___end object histogram___`, with a block per object type containing `Key memory used`, `Values - Total Memory used`, optional `Cardinality histogram`, `Items length histogram`, and an optional **`Listpack histogram`** — the last is the practical substitute for `OBJECT ENCODING` || [SRC `debugcmd.cc:1151-1170`]
- `MEMORY USAGE` is **partially** supported: `SAMPLES` is accepted but ignored; Dragonfly adds a `WITHOUTKEY` option || [DOC command-reference/server-management/memory-usage]
- **`MEMORY USAGE` returns `0` for small values stored inline in the key table** — a real trap when sizing key layouts || [DOC] "Such values have no allocation of their own, so `MEMORY USAGE` reports `0` for them."
- `--listpack_max_bytes` / `--listpack_max_field_len` appear on the docs flags page but **do not exist at v1.34.0** — post-v1.34.0 exposures of `max_listpack_map_bytes` / `max_map_field_len`.
- Only three flags are runtime-mutable via `CONFIG SET` here || [SRC `server_state.cc:239` `GetMutableFlagNames`] "base::GetFlagNames(FLAGS_rss_oom_deny_ratio, FLAGS_serialization_max_chunk_size, FLAGS_max_squashed_cmd_num);"
- Hard ceilings: up to 2^16 arguments per command (`--max_multi_bulk_len`), 256MB per argument, 2 billion values per list/set/hash, 32-bit TTLs || [DOC managing-dragonfly/known-limitations]
- Strings capped at 256MB; expirations capped at 8 years, and sub-second PEXPIRE values above 2^28ms are rounded || [SRC `docs/differences.md`] "String sizes are limited to 256MB."

---

## 7. `.claude/skills/` in the Dragonfly repo

- **At tag v1.34.0 there is no `.claude/` directory at all.** `GET /contents/.claude?ref=v1.34.0` → 404; `ls -d .claude` in the v1.34.0 checkout exits non-zero. || [SRC]
- On `main` (checked 2026-09-15) both skills exist but were added **~2026-06, nine months after v1.34.0**: `benchmark` (added 2026-06-18, #7642), `benchmark-report` (2026-06-25, #7706), plus `reproduce-fuzz-crash`, `hooks/format-after-edit.sh`, `settings.json`. **DOES NOT APPLY to v1.34.0.**

**`benchmark/SKILL.md`** (https://raw.githubusercontent.com/dragonflydb/dragonfly/main/.claude/skills/benchmark/SKILL.md) — six phases:
gather spec → prepare instances → network tuning → start monitoring → run workloads → metrics/charts/report.
Load generators `dfly_bench` and `memtier_benchmark`; compares against Valkey/Redis in docker.
Hard rules: client on a **separate machine**; always target the **private IP**; always run servers on **port 6380**.
Dataset must fill ~60-75% of RAM; `--key_maximum` computed from RAM ÷ bytes-per-entry.
Prefill writes use `--pipeline 30`; drop to `--pipeline 1` only for latency-honest read benchmarks.
Zero-error rule: `capture_result.sh` exits non-zero on any eviction error → cut `--key_maximum` 10-15% and re-run.
Mandatory scripts: `bench_server.sh`, `monitor_fill.sh` (RSS guard, abort 92%), `capture_result.sh`, `collect_metrics.py`,
`make_charts.py`, `plot_fill.py`. Prometheus scrapes at 1s. Every output dir needs `commands_audit.log` with exact commands.
Notes `MIMALLOC_ALLOW_LARGE_OS_PAGES=1` makes host `free` diverge from `used_memory_rss`.

**`benchmark-report/SKILL.md`** (same path, `benchmark-report`) — a pure formatter; turns a directory of raw
memtier/redis-benchmark output into `report.md` and does **not** run benchmarks. Step 0 mandates a 3-4 question interview
(scenario, instance types, storage config, pricing/region) before writing. Parses `KEY_MAXIMUM, DATA_SIZE, PIPELINE,
CLIENTS, THREADS, TEST_TIME, FILL_RATIO, ACCESS_RATIO` from scripts and `Ops/sec`, `Average Latency`, `p50/p99/p99.9`
from memtier JSON. Identifies the system under test by filename/hostname. Charts via `tools/plot_memtier_latency.py`.
Fixed template; comparisons as `(A-B)/B*100`; banned words include "blazing fast", "significantly", "game-changing";
never include hostnames/endpoints; missing context becomes `[TODO: ...]`, never a guess.

Relevance to a Lua skill: neither skill is about scripting. They are useful only as house style for benchmarking and reporting.

---

## 8. Lua limitations and differences vs Redis

- Lua **5.4.4** (Redis uses 5.1); integers are a real type || [SRC `docs/differences.md`]
- Libraries loaded: base, table, string, math, debug, plus `cjson`, `struct`, `cmsgpack`, `bit` || [SRC `interpreter.cc:352-361`] "LoadLibrary(lua, "cjson", luaopen_cjson); LoadLibrary(lua, "struct", luaopen_struct); LoadLibrary(lua, "cmsgpack", luaopen_cmsgpack); LoadLibrary(lua, "bit", luaopen_bit);" — **no `os`, `io`, or `package`**; the `debug` global is set to `nil` after setup.
- `redis` table members: `call`, `pcall`, `acall`, `apcall`, `sha1hex`, `error_reply`, `status_reply`, `replicate_commands`, `log`. A `dragonfly` global also provides `ihash` and `randstr`. || [SRC `interpreter.cc:664-710`]
- **`redis.setresp` is NOT registered — it does not exist in v1.34.0** (`grep setresp src/core/interpreter.cc` → no match). || [SRC]
- `redis.replicate_commands` is a **no-op stub** — Dragonfly replicates script **effects**, so the call is meaningless but harmless || [SRC] "/* no-op functions */ /* redis.replicate_commands*/"
- `redis.log` exists but is **silent** without `--lua_enable_redis_log`, and was only added in **v1.33.0** (#5672) || [SRC + releases]
- `redis.error_reply` / `redis.status_reply` return single-field tables `{err=...}` / `{ok=...}` || [SRC] "int RedisErrorReplyCommand(lua_State* lua) { return SingleFieldTable(lua, "err"); }"
- **`redis.error_reply` does NOT auto-prefix `ERR`.** The serializer only prepends `-` if absent || [SRC `main_service.cc` `EvalSerializer::OnError`] "if (!str.empty() && str.front() != '-') { rb_->SendError(absl::StrCat("-", str)); }" — errors surfacing *from* a `redis.call` do get `-ERR `.
- `call` raises and aborts, `pcall` returns the `{err=...}` table — matches Redis || [SRC `CallRedisFunction`, `raise_error` flag]
- Errors raised by Dragonfly's own Lua helpers carry a `source: line: ` trace prefix, so error strings are **not** byte-identical to Redis || [SRC] "string msg = absl::StrCat(dbg.source, ": ", dbg.currentline, ": ", error);"
- **No `redis.LOG_*` constants exist** — pass numeric levels 0..3. Bad input raises "Invalid log level." or "redis.log() requires two arguments or more." || [SRC `interpreter.cc:563,573`]
- **`redis.breakpoint`, `redis.debug`, `redis.set_repl` and `REPL_*` constants are all absent.** `SCRIPT DEBUG` is Unsupported || [SRC + DOC compatibility]
- **Replies into Lua are always flattened to RESP2 shapes**: a MAP reply becomes a flat 2N array; there is no RESP3 map/set/double Lua representation || [SRC `InterpreterReplier::StartCollection`] "if (type == MAP) len *= 2; explr_->OnArrayStart(len);"
- **The Lua→reply translator rejects return values nested deeper than 128** || [SRC `interpreter.cc:926` `Interpreter::IsTableSafe`] "if (lua_checkstack(lua_, 3) == 0 || depth > 128) return false;"
- `numkeys` validation gives two distinct errors || [SRC `main_service.cc:485`] "if (!absl::SimpleAtoi(num_keys_str, &num_keys) || num_keys < 0)" and "Number of keys can't be greater than number of args"
- An infinite/long script **pins its thread and cannot be interrupted** — no instruction hook, no `SCRIPT KILL`, no `lua-time-limit`, no `busy-reply-threshold`, no BUSY reply; every client on that thread hangs and recovery is `kill -9` || https://github.com/dragonflydb/dragonfly/issues/8269 (OPEN, filed 2026-09-09 by contributor vyavdoshenko, found running the Valkey TCL scripting suite). Filed after v1.34.0 but **applies to v1.34.0**, which has no SCRIPT KILL.
- Compatibility table rates EVAL / EVAL_RO / EVALSHA / EVALSHA_RO / SCRIPT LOAD / SCRIPT EXISTS "Fully supported", but warns that rating is command-surface only || [DOC compatibility] "'Fully supported' does not imply byte-for-byte identical behavior."
- Atomicity is real by default || https://www.dragonflydb.io/blog/leveraging-power-of-lua-scripting "Lua scripts are executed atomically. This means that a script is a single, indivisible operation which runs from start to finish without any other operation interrupting it."
- KEYS/ARGV are set as Lua globals per call and the stack is reset after || [SRC] "interpreter->SetGlobalArray("KEYS", eval_args.keys); interpreter->SetGlobalArray("ARGV", eval_args.args);" — **no explicit Dragonfly-specific KEYS/ARGV count limit found in source; `unpack` limits are Lua 5.4's own (`LUAI_MAXSTACK`), UNVERIFIED from an allowed source.**
- Script errors are recorded per-SHA and echo the SHA || [SRC] "string resp = StrCat("Error running script (call to ", eval_args.sha, "): ", error);" — v1.34.0 includes "fix: script error reply" (#5776).
- **No script timeout and no `SCRIPT KILL`**: `grep -rn "lua_timeout\|SCRIPT KILL\|script_kill\|kBusyScript" src/` returns **zero matches**; `SCRIPT HELP` lists no KILL. Docs compatibility table agrees: "SCRIPT KILL | Unsupported". A slow script cannot be interrupted. || [SRC + DOC]
- **Redis Functions are entirely absent**: "FCALL | Unsupported", "FUNCTION * | Unsupported" || [DOC compatibility]
- `SCRIPT FLUSH` is "Partially supported | Missing: ASYNC, SYNC" || [DOC compatibility]
- `--lua_resp2_legacy_float` truncates doubles toward zero. Despite the name, in v1.34.0 the check lives in the reply translator's `OnDouble` and is **not** gated on RESP version — this is v1.34.0's "Enable lua legacy float response for RESP3" (#5754) || [SRC `main_service.cc:359`] "if (GetFlag(FLAGS_lua_resp2_legacy_float)) { const long val = d >= 0 ? static_cast<long>(floor(d)) : static_cast<long>(ceil(d)); rb_->SendLong(val); }"

---

## 9. Release notes v1.20 → v1.34 mentioning Lua / scripts / squashing / tx locking

Source: https://github.com/dragonflydb/dragonfly/releases (read via the releases API, 121 releases, CHECKED 2026-09-16).
`hashtag` and `interpreter` appear **zero times** in any release note in this band.

**Lua / EVAL:** v1.20.0 (2024-07-09) lua+client tracking (#3163); don't transactionalize empty EVAL inside EXEC (#3231);
return which undeclared key was accessed (#3245) · v1.21.0 (2024-08-07) implement SCRIPT GC (#3431) ·
**v1.22.0 (2024-09-03) "Introduce Dragonfly specific lua pragmas" (#3517) — origin of `--!df flags=`**; allow
pre-declaring Lua SHAs to run with undeclared keys (#3465) · v1.23.0 script error → warning (#3670) · v1.25.0 script load
inside multi (#4074) · v1.26.0 memory leak on lua error (#4236) · v1.26.2/v1.27.0 **add Lua force atomicity flag (#4523)** ·
v1.26.4/v1.27.0 db_index in EVAL transactions (#4586) · v1.29.0 (2025-04-21) Lua stack buffer overflow crash (#4853/#4854) ·
**v1.31.0 (2025-06-19) add Lua GC flags (#5194) — origin of `--luagc`** · v1.32.0 (2025-08-04) CVE-2020-14147 integer
overflow (#5421); `lua_mem_gc_threshold=0` disables forced GC (#5587); **lua_undeclared_keys_shas gains the Sentry SHA (#5511)** ·
**v1.33.0 (2025-08-19) add `redis.log` (#5672)** · **v1.34.0 lua legacy float response for RESP3 (#5754); fix script error reply (#5776)**

**Squashing:** v1.22.0 make pipeline_squash configurable (#3529) · v1.25.1/v1.26.0 regression determining eval commands
(#4116) · v1.26.0 INFO memory for squashing replies (#4147), stats (#4132), slowlog (#4138) · v1.26.1 RESP3 ignored under
squashing (#4400) · v1.27.0 slot calculation during squashing (#4460) · **v1.29.0 add flag `max_squashed_cmd_num` (#4964)**;
pass max_squash_size via option (#4960) · v1.30.0 cleanups (#5011) · v1.31.0 bonus key + init error (#5303) · v1.32.0 avoid
squashing when reply size crosses limit (#4924) · v1.33.0 metrics under squash_stats_latency_lower_limit (#5659), timings
for slow squash hops (#5679) · **v1.33.1/v1.34.0 stack corruption in MultiCommandSquasher (#5697)**

**Transaction scheduling / locking:** v1.21.0 db_slice lock vs preemptions (#3406), namespace access (#3364), block cancel
status (#3371) · v1.22.0 don't set continuation for blocking (#3540) · v1.23.0 zset store conclude on error (#3755) ·
**v1.24.0 (2024-10-16) lock keys for optimistic transactions (#3865)** · v1.27.4/v1.28.0 auto journaling in transaction
(#4737) · **v1.28.0 (2025-03-17) highlight "Deadlocked transaction bugs #4590 #4647 #4685"** · v1.29.0 skip heartbeat under
global lock (#4882) · v1.31.0 DCHECK in non-atomic tx (#5217), non-transactional multi/exec access (#5260), schedule queues
(#4925), KEYLOCK_ACQUIRED crash for NO_KEY_TRANSACTIONAL (#5185)

---

## 10. Observability — measuring whether a script is on the fast path

`INFO` fields || [SRC `server/server_family.cc`]:
- **`eval_shardlocal_coordination_total`** — scripts that took the fast single-shard path (no per-call hops). Want this to dominate.
- **`eval_io_coordination_total`** — scripts that took the multi-shard/hop path. The ratio is the primary scripting health metric.
- `eval_squashed_flushes` — times the async (`acall`) buffer was flushed.
- `blocked_on_interpreter` — connections **currently** blocked waiting for an interpreter; `lua_blocked_total` — cumulative.
- `lua_interpreter_cnt`, `lua_interpreter_return`, `lua_force_gc_calls`; `tx_queue_len`, `tx_with_freq`, `squash_with_freq`.
- Prometheus also exports `lua_interpreter_cnt` and `lua_blocked_total` || [SRC `server_family.cc:1670,1675`]

- **When all `--interpreter_per_thread` (10) interpreters on a thread are busy, further script calls BLOCK** || [SRC `interpreter.cc:1249` `InterpreterManager::Get`] "bool blocked = waker_.await([this]() { return !available_.empty(); }); tl_stats().blocked_cnt += (uint64_t)blocked;" — long scripts starve the pool for their whole thread. Rising `lua_blocked_total` ⇒ raise `--interpreter_per_thread` or shorten scripts.

---

## Could not verify from an allowed source

- **`SCRIPT LATENCY` histogram bucket boundaries** — internal to `base::Histogram` in the helio dependency, outside the allowed source list.
- **Exact `unpack` element limit** — Redis's 8000-element `LUAI_MAXCSTACK` patch is provably **absent** (repo-wide code search: 0 hits), so Redis's "too many results to unpack" threshold does not carry over. Lua is an external CMake dependency with no visible config in the repo, so the effective Lua 5.4 ceiling is UNVERIFIED. Per-command arg ceilings (2^16 args, 256MB each) are documented and do bound KEYS+ARGV.
- **Whether the docs' sandbox restrictions (protected `setmetatable`, text-only `load()`) were already present in v1.34.0** — the docs page is unversioned and postdates the tag.
- **`#!lua` tolerance** — no evidence either way that Dragonfly accepts Redis's shebang for compatibility; the v1.34.0 regex matches only `--!df flags=`.
- **Effects-vs-script replication as an explicit documented statement** — source evidence is strong and one-directional (effects only: `replicate_commands` is a no-op, no `set_repl`, no `REPL_*`, journaling is per-command via `Transaction::LogAutoJournalOnShard`), but no docs page or blog says so in those words.
- **There is no docs page titled "differences from Redis."** The nearest equivalents are the compatibility table and Known Limitations; neither covers Lua semantics. All Lua-specific incompatibilities here are source-derived.
- No Dragonfly blog post on VLL or the transactional framework exists; that material lives only in the repo docs.
