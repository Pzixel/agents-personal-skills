# Dragonfly ecosystem — second sweep (SOURCES rows 29–70)
Scope: dragonflydb.io blog + docs, the `dragonflydb/dragonfly` repo `docs/` folder and maintainer
PRs/issues, and the VLL paper. Target: facts that change how one *writes* a Lua operation, *lays out
keys*, or *measures* a script on **v1.34.0**. `[V]` = verified against the v1.34.0 checkout at
`/tmp/df134`. `[POST-1.34]` = true on `main`, NOT in v1.34.0 — do not teach as current behaviour.

---

## Top 10 decision-changing facts from this sweep

1. **`CL.THROTTLE` is native — the most common Lua script (rate limiting) should not be Lua here.**
   `[V]` `string_family.cc:1702`: `CO::WRITE | CO::DENYOOM | CO::FAST, -5, 1, 1` — one key, single
   shard, FAST. Zero hops, zero interpreter slots.
   https://www.dragonflydb.io/blog/introducing-a-rate-limiting-api-by-dragonfly
2. **Under `--cluster_mode=yes` a script's keys MUST share one slot** ("computed from the key name
   using the CRC16 algorithm and then taking the modulus of 16,384"; cross-slot → `-MOVED`). Hashtag
   co-location is a correctness requirement, not just a perf tweak.
   https://www.dragonflydb.io/blog/a-preview-of-dragonfly-cluster
3. **Expiry caps at 8 years and ms TTLs lose precision**: "expirations greater than 2^28ms are quietly
   rounded to the nearest second." Lock/limiter scripts computing ms TTLs >~74h diverge from Redis.
   https://github.com/dragonflydb/dragonfly/blob/main/docs/differences.md
4. **A multi-key command is already split per shard** — "Every subcommand contains keys for one
   relevant shard and executes on the relevant thread". A 3-shard `MGET` costs what 3 `redis.call`
   hops cost; wrapping it in Lua saves nothing unless keys co-locate.
   https://www.dragonflydb.io/guides/redis-and-dragonfly-architecture-comparison
5. **VLL requires the full read/write set up front** — the upstream reason `KEYS` must be complete, and
   why undeclared keys must degrade to a global lock. https://www.cs.umd.edu/~abadi/papers/vldbj-vll.pdf
6. **Squashing is a CPU hot path, not a free win.** romange: `MultiCommandSquasher` folds ~30 single-shard
   commands into one hop but "requires lots of cpu"; pprof shows `SquashPipeline` at ~20% CPU. A
   shard-local script beats a squashed pipeline. https://github.com/dragonflydb/dragonfly/issues/6006
7. **`SCRIPT LOAD` borrows a Lua interpreter** (budget: `interpreter_per_thread`=10 `[V]`), and on
   v1.34.0 holds it across a journaling hop. Measured contended mean 620.78 ms → 259.90 ms after the
   post-1.34 fix. Load scripts once at startup, never per worker boot.
   https://github.com/dragonflydb/dragonfly/pull/8300
8. **A key costs ~18.6 B overhead vs Redis 32–48 B, with no rehash spike** (fixed 840-entry segments).
   "At 100 million keys, Redis consumes roughly 7.5 GB ... while Dragonfly uses about 4.3 GB". This
   weakens the Redis habit of collapsing many small keys into one big hash.
   https://www.dragonflydb.io/blog/from-dict-to-dashtable-how-dragonfly-cuts-memory-overhead-by-40
9. **Skew is a shard problem; a hashtag pins heat to one thread.** Zipfian across 100 nodes overloads the
   hottest shard, while "a cluster of 10 powerful instances" gives a 1.27x hot/cold ratio. Co-locate for
   hops, then check the tag's cardinality exceeds shard count.
   https://www.dragonflydb.io/blog/the-hidden-bottlenecks-of-scaling-out
10. **The `eval_*`/`lua_*` counters are undocumented and not in Prometheus.** The INFO reference lists no
    `eval_shardlocal_coordination_total`/`eval_io_coordination_total`/`lua_blocked_total`, and "Batch I/O
    counters are available through `INFO stats`, but are not currently exported by the Prometheus
    endpoint." Scrape `INFO ALL`. https://www.dragonflydb.io/docs/managing-dragonfly/monitoring

---

## Entries

### 29. Ensuring Atomicity: A Tale of Dragonfly Transactions — C
https://www.dragonflydb.io/blog/transactions-in-dragonfly
> "The sequential numbers of couriers are reflected as transaction IDs (txid), assigned by
> incrementing a single global atomic counter."
A transaction holding a shard is a *continuation*; intent locks "declare a future intention". Changes:
a long script is a continuation on its shard, so everything else on that shard queues behind it — the
mechanism behind interpreter starvation. Confirms Lua and MULTI share one machinery.
### 30. Batch Operations: Pipelining, Transactions, and Lua Scripting — C
https://www.dragonflydb.io/blog/batch-operations-in-dragonfly
Only official page ranking the three batching tools.
> "Dragonfly processes a single large pipeline twice as fast as normal when using a proper
> pipeline_squash configuration."
Changes: default to a pipeline unless you need a *decision* made server-side on a value you just read;
squashing already gives the single-hop benefit without consuming an interpreter slot.
### 31. Scaling Redis without Clustering — C
https://www.dragonflydb.io/blog/scaling-redis-without-clustering
> "A key limitation in Redis Cluster is that it cannot handle multi-key operations unless the
> involved keys share the same hashtag."
Vertical headroom quoted as "up to 128 cores and 1TB of memory". Changes: on single-node Dragonfly a
script *may* legally span shards — but legal is not cheap (fact #4).
### 32. Docs — Cluster Mode — A
https://www.dragonflydb.io/docs/managing-dragonfly/cluster-mode
> "It distributes keys in the same way as Valkey Cluster" and "supports hash tags in the same way as
> Valkey Cluster."
> "Multi-shard cluster mode supports only database 0."
Changes: `SELECT`-based tenant separation is incompatible with a future multi-shard cluster.
### 33. From dict to DashTable — C
https://www.dragonflydb.io/blog/from-dict-to-dashtable-how-dragonfly-cuts-memory-overhead-by-40
> "A segment can store up to 840 entries. That fixed capacity is what makes the split cost bounded"
~16 B slot + ~2.59 B segment ≈ 18.59 B/entry; growth in "small bounded steps instead of large
table-level phases". See fact #8.
### 34. Repo — docs/dashtable.md — B
https://github.com/dragonflydb/dragonfly/blob/main/docs/dashtable.md
> "By leveraging DashTable compartmentalized structure it can actually employ a very efficient
> passive expiry algorithm with low CPU overhead."
A segment is full only when home bucket, neighbour, and all 4 stash buckets are full. Changes: expiry
is partly *passive* (done during bucket scans/splits) — a script may assume reads never see an expired
key, but not that memory was already reclaimed.
### 35. Repo — docs/df-share-nothing.md — B
https://github.com/dragonflydb/dragonfly/blob/main/docs/df-share-nothing.md
> "Each database shard is owned and accessed by a single thread."
> "It also offers strict serializability for Lua scripts and multi-command transactions."
Inter-thread interaction is message passing only. Changes: strict serializability for scripts is stated
officially — you can rely on it, and its price is the hop you are trying to avoid.
### 36. VLL: a lock manager redesign for main memory database systems — D
https://www.cs.umd.edu/~abadi/papers/vldbj-vll.pdf
Transactions declare their full read/write set in advance; per-record counters plus a blocked/free split
replace a lock manager. Transactions that cannot declare their set need special handling — exactly what
`allow-undeclared-keys` → global lock is.
### 37. The Hidden Bottlenecks of Scaling Out — C
https://www.dragonflydb.io/blog/the-hidden-bottlenecks-of-scaling-out
> "an application running `GET` commands that return 25 KB values can expect a maximum RPS of 3,750
> on a `m6g.large` instance type due to its 0.75 Gbps bandwidth limit."
Changes: if a script returns a large aggregate, bandwidth — not shards — is the ceiling. Return a
decision, not a payload. Plus the Zipfian skew math in fact #9.
### 38. Rate Limiting with Dragonfly (CL.THROTTLE) — C
https://www.dragonflydb.io/blog/introducing-a-rate-limiting-api-by-dragonfly
> "CL.THROTTLE emailGW 20 120 60 1 … emailGW is a bucket with a capacity of 20 leaking at the rate
> of 120 unites per 60 seconds"
> "Rate limiting parameters are provided with every call to CL.THROTTLE so that limits can easily be
> reconfigured on the fly"
Returns a 5-element array: limited(0/1), limit, remaining, retry-after, reset. See fact #1.

### 39. Docs — INFO command reference — A
https://www.dragonflydb.io/docs/command-reference/server-management/info
> "total_pipelined_commands: Total number of commands pipelined to the server"
Dragonfly-specific memory fields: `object_used_memory, table_used_memory, num_buckets, inline_keys,
listpack_blobs`. Changes: `total_pipelined_commands` proves clients actually pipeline; the script
counters are source-only (fact #10).
### 40. Docs — Sidekiq integration — A
https://www.dragonflydb.io/docs/integrations/sidekiq
> "With this configuration, Dragonfly equally spreads the Sidekiq queues (lists) among the available
> shard threads."
**CONFLICT `[V]`:** `sharding.cc:18` in v1.34.0 declares `--shard_round_robin_prefix` as
`"Deprecated -- will be removed"` and logs a warning; the docs carry no deprecation notice.
Corroborates round 1 — do not recommend the flag. The *concept* survives: a few hot same-prefix keys
is the one case where you want anti-co-location.
### 41. Docs — Redlock integration — A
https://www.dragonflydb.io/docs/integrations/redlock
> "To release the lock, the client application uses a Lua script, which involves the `GET` and `DEL`
> commands, to perform compare-and-delete operations."
Changes: the canonical unlock script is single-key → shard-local fast path, no flags needed. A clean
worked example of a script that needs no tuning at all.
### 42. Docs — Known Limitations — A
https://www.dragonflydb.io/docs/managing-dragonfly/known-limitations
> "Each command can contain up to `2^16` arguments (can be changed with the `max_multi_bulk_len` flag)."
Also 2B keys per CPU thread, 256MB per argument/string. Changes: 65,536 is the hard ceiling on
`KEYS`+`ARGV` combined — the real bound on batch size per EVAL invocation.
### 43. Docs — Monitoring — A
https://www.dragonflydb.io/docs/managing-dragonfly/monitoring
> "exposes Prometheus-compatible metrics at `:6379/metrics`. These include metrics for connection
> memory and pipelines."
> "Batch I/O counters are available through `INFO stats`, but are not currently exported by the
> Prometheus endpoint."
See fact #10.

### 44. Running and Optimizing Sidekiq Workloads with Dragonfly — C
https://www.dragonflydb.io/blog/running-sidekiq-with-dragonfly
Ladder: Redis ~140k jobs/s → Dragonfly baseline 148,646 → round-robin 320,377 → optimized 487,781.
> "The biggest benefit of this optimization, besides the reduction of hops, is that now blocking
> transactions can both be inline and quick."
Changes: states the objective in the right currency — reduce hops, keep transactions inline — which is
exactly the goal of the shard-local Lua fast path.
### 45. Threading Models Matter: Dragonfly vs Valkey — C
https://www.dragonflydb.io/blog/why-threading-models-matter-dragonfly-vs-valkey
> "each CPU core responsible for its own subset of keys. This means there's no global lock and no
> single main thread coordinating all data operations"
Benchmark shape: `c8g.2xlarge`, 50M ZADD, pipeline depth 1000, 36-byte values, **100 distinct keys**.
Changes: 100 keys on an 8-shard box is a skew test — benchmark a script with enough distinct keys or
you measure one shard.
### 46. Scaling Heavy BullMQ Workloads with Dragonfly Cloud (part 3) — C
https://www.dragonflydb.io/blog/running-bullmq-with-dragonfly-part-3-cloud
> "start Dragonfly with the following server flags: `--cluster_mode=emulated --lock_on_hashtags`"
Naming scheme: `bullmq:{email-group-001}:email-queue-001` — the tag is a *queue group*, so many queues
share a tag deliberately. Changes: set tag granularity = unit of atomicity, then confirm tag count
exceeds shard count (fact #9).
### 47. 6.43M RPS on 64-core Graviton3 — C
https://www.dragonflydb.io/blog/dragonfly-achieves-6-million-rps-on-64-core-graviton3
> "we take the maximum ops/sec value with a P99.9 latency of less than 10ms as the final result set."
Prefill 10M keys, sweep client threads/connections. Changes: copy this protocol for script benchmarks —
report max throughput subject to a P99.9 SLO, not raw max.
### 48. Docs — Replication — A
https://www.dragonflydb.io/docs/managing-dragonfly/replication
> "Dragonfly defines the replication lag as the maximal amount of unacknowledged database among all shards"
Gap: the page does **not** state command-vs-effect replication. Round 1's source-derived conclusion
(effects only) stays source-only — no official page confirms it in words.
### 49. Developing with Dragonfly: Solve Caching Problems — C
https://www.dragonflydb.io/blog/developing-with-dragonfly-part-02-solve-caching-problems
> "The lock mechanism ensures that, under high concurrency, only one goroutine performs the refresh operation."
> "introducing a random jitter to the expiration time of each cached key can be beneficial"
Changes: Dragonfly's own recommended stampede fix is a client-side single-flight lock + TTL jitter +
pipelined GET/TTL — not a Lua script. Another "don't write the script" data point.
### 52. Rueidis with Dragonfly: Auto-Pipelining & Client-Side Caching — C
https://www.dragonflydb.io/blog/rueidis-client-side-caching
> "We now support the `OPTIN`, `OPTOUT`, and `NOLOOP` subcommands for the `CLIENT TRACKING` command.
> Additionally, we've introduced the `CLIENT CACHING YES/NO` command."
Changes: read-mostly lookups people wrap in Lua to save round trips can be served from a tracked
client-side cache instead. Verify the release against v1.34.0 before relying on OPTIN.
### 53. Cutting Celery & Sidekiq queue memory 3–4x with dictionary compression — C
https://www.dragonflydb.io/blog/how-dragonfly-cuts-celery--sidekiq-queue-memory-by-3-4x-with-dictionary-compression
> "With structured data like JSON this is a huge win, because things like keys are repeated more often
> than not, and a dictionary once trained will contain most of the keys."
**Lists only**, gated on `list_experimental_zstd_dict_threshold` (16 KiB recommended). `[V]` absent from
v1.34.0. `[POST-1.34]` Changes (when available): JSON job payloads belong in a list, not N hash fields.
### 54. Docs — Point-in-Time Snapshotting Design — A
https://www.dragonflydb.io/docs/managing-dragonfly/snapshotting
> "To allow concurrent writes during the snapshotting phase, we setup a hook that is triggerred on
> each entry mutation in the table."
Each shard-thread serializes only its own data. Changes: writes from a script during a snapshot pay an
extra per-entry hook, so a write-heavy script's latency is not stationary — measure across a snapshot.
### 58. A Preview of Dragonfly Cluster — C
https://www.dragonflydb.io/blog/a-preview-of-dragonfly-cluster
See fact #2. Also: slot migration is concurrent and
> "data from migrated slots is permanently erased from the source node."

### 59. Guides — Redis and Dragonfly Architecture Comparison — C
https://www.dragonflydb.io/guides/redis-and-dragonfly-architecture-comparison
See fact #4. Also states VLL queueing plainly: "Transactions attempting to access locked keys are
queued until all required keys become available."
### 60. One Instance, Ten Workloads: ACL Database Selectors — C
https://www.dragonflydb.io/blog/one-dragonfly-instance-ten-workloads-how-acl-database-selectors-work
> "If app1 tries to issue a SELECT 2 command, it gets a NOPERM error, not just a convention violation"
Changes: tenants pinned via `$<db>` mean a script inherits the connection's database — and the scheme
is unavailable under multi-shard cluster (entry 32). Whether ACL key patterns are enforced against a
script's `KEYS` is not addressed; unresolved.
### 61. Repo — docs/differences.md — B
https://github.com/dragonflydb/dragonfly/blob/main/docs/differences.md
**Fills a round-1 gap**: round 1 concluded no "differences from Redis" page exists — it does, in the
repo rather than on the site.
> "We use lua 5.4.4 that has been released in 2022. That means we also support lua integers."
> "EXPIRE, PEXPIRE, EXPIREAT and PEXPIREAT accept NX together with GT or LT: the expiry is set when
> the key has none, otherwise GT or LT alone decides."
Plus the 8-year / 2^28ms rounding rule (fact #3) and `SORT` ignoring locale.
### 62. Repo — docs/namespaces.md — B
https://github.com/dragonflydb/dragonfly/blob/main/docs/namespaces.md
> "Some features are not supported for non-default namespaces, such as replication and save to RDB"
A `Namespace` holds a `vector<DbSlice>`, one per shard. Changes: experimental and loses
replication/RDB — not a viable tenant-isolation layout; prefer key prefixes, which keep hashtags usable.
### 63. PR #6991 — pcre2 regex & enable auto async — B (dranikpg)
https://github.com/dragonflydb/dragonfly/pull/6991
> "Make auto async use pcre2 for regexes. PCRE2 uses limited stack space, whereas std::regex is not
> suited for the small fiber stacks that we have"
Changes: `--lua_auto_async` works by **regex over the script text**, not by AST analysis. Write plain,
recognizable `redis.call(...)` forms if you want it to fire; obfuscated dispatch (e.g. `local f =
redis.call; f(...)`) will not be rewritten.

### 64. PR #6277 — Locking control from lua scripts — B (dranikpg) `[POST-1.34]`
https://github.com/dragonflydb/dragonfly/pull/6277
> "Add `dragonfly.lock` and `dragonfly.unlock` functions to manage the underlying transaction ... I
> assume all those function calls will be carefully placed by someone from our team"
`[V]` absent from v1.34.0 (`grep "dragonfly.lock" src/` → no hits). Note the author's own caveat:
intended for internal use. Do not teach it.

### 65. PR #8300 — fix: script load interpreter wait — B (BorysTheDev) `[POST-1.34]`
https://github.com/dragonflydb/dragonfly/pull/8300 — see fact #7.

### 66. PR #8321 — fix: reduce script load contention — B (BorysTheDev) `[POST-1.34]`
https://github.com/dragonflydb/dragonfly/pull/8321
> "Releases a borrowed Lua interpreter immediately after SCRIPT LOAD compilation, before its
> journaling transaction hop."
Changes: on v1.34.0 an interpreter is held **across a journaling hop** during `SCRIPT LOAD`. Loading
scripts from many connections at once on a replicated instance is a self-inflicted stall.
`[V]` `interpreter_per_thread` default is 10 (`server_state.cc:26`) — that is the whole budget.

### 67. Issue #6006 — Redesign pipelining support — B (romange) — see fact #6
https://github.com/dragonflydb/dragonfly/issues/6006

### 68. Issue #7651 — Improve pipeline performance — B — see fact #6
https://github.com/dragonflydb/dragonfly/issues/7651
pprof: `11.86s 19.95% facade::Connection::SquashPipeline`. Commenter identity unverified; the pprof
numbers are quoted from the issue body.

### 70. Repo — docs/ directory listing — B
https://github.com/dragonflydb/dragonfly/tree/main/docs
Inventory: `README.md`, `build-from-source.md`, `dashtable.md`, `dense_set.md`, `df-share-nothing.md`,
`differences.md`, `faq.md`, `memcached_benchmark.md`, `namespaces.md`, `rdbsave.md`, `transaction.md`.
`dense_set.md`, `rdbsave.md`, `faq.md` remain unread — see gaps.

---

## Rejected (rows 50, 51, 55, 56, 57, 69)

- **Modern Distributed Database Architectures Part 1** — generic primer; no VLL, no scheduling.
- **Redis Analysis Part 1: Threading Model** — 2022 `midi-redis` throughput post; no per-command cost.
- **Docs — DEBUG command reference** — documents zero subcommands: "The `DEBUG` command is an internal
  command." No `DEBUG OBJECT`/`POPULATE`/`SHARDS` detail available from an allowed source.
- **Announcing Dragonfly** — launch narrative; no dashtable/VLL/shard detail despite the topic.
- **dragonfly-vs-redis comparison page** — marketing table (3.9M vs 150K QPS); no scripting semantics.
- **Issue #6629 (BullMQ "Missing lock")** — the `allow-undeclared-keys` explanation in the issue body
  is AI-generated and **disputed by romange**: "the 'explanation' seems to be AI generated and hints
  that it's related to `allow-undeclared-keys` which seems misleading to me". Not authoritative.

---

## Gaps still open after this sweep

- **`SCRIPT LATENCY` bucket format** — still unresolved. It lives in `base::Histogram` in `romange/helio`;
  I did not reach a readable helio source page this round.
- **Effects-vs-command replication** — no official page says it in words (entry 48). Source-only.
- **`eval_io_coordination_total` / `eval_shardlocal_coordination_total` / `lua_blocked_total`** — exist in
  source, documented nowhere and not in `/metrics` (fact #10). The skill must tell users to read `INFO ALL`.
- **ACL key patterns vs a script's `KEYS`** — whether `~pattern` is enforced against declared keys:
  not documented anywhere I found.
- **`--interpreter_per_thread` and `--lock_on_hashtags` have no dedicated docs page**; the flags page is
  the only official surface, and it disagrees with source on `shard_round_robin_prefix` (entry 40).
- **Unread repo docs**: `dense_set.md` (set/hash encoding — would inform "hash vs many keys"),
  `rdbsave.md`, `faq.md`.
- **No conference talk with published slides/transcript hosted on dragonflydb.io or the repo** was found;
  the events page (`/events/...`) entries are video/registration pages without transcripts.
