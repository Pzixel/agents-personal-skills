# Lua patterns in the wild — how mature projects structure server-side Lua and key layouts

Ecosystem evidence for a skill about writing Lua/EVALSHA on **Dragonfly v1.34.0** (Lua 5.4, every key
declared in `KEYS`, single-shard scripts avoid coordinator hops, no `FUNCTION`/`FCALL`, no `SCRIPT KILL`).
Stars verified via `gh api` 2026-09-16; 23 `SOURCES.md` rows carry this report name.

## A. Lua-heavy application libraries

### 1. Sidekiq — `lib/sidekiq/scheduled.rb` (13,556 stars)
https://github.com/sidekiq/sidekiq/blob/main/lib/sidekiq/scheduled.rb

Canonical minimal NOSCRIPT protocol: SHA cached in an instance var, `SCRIPT LOAD` lazily on first use,
`rescue` on the `NOSCRIPT` **prefix**, nil the SHA, `retry`. The script is 6 lines, one key, one ARGV.

```ruby
rescue RedisClient::CommandError => e
  raise unless e.message.start_with?("NOSCRIPT")
  @lua_zpopbyscore_sha = nil
  retry
```

Deliberate **anti-batching**: `enqueue_jobs` pops **one** job per EVALSHA — "We need to go through the
list one at a time to reduce the risk of something going wrong between the time jobs are popped ... and
losing the jobs." Batch size is a durability decision, not a throughput decision.

### 2. RQ — `rq/scripts.py` (10,686 stars)
https://github.com/rq/rq/blob/master/rq/scripts.py

Best-in-class **script header contract**: every script opens with a comment block binding each slot.

```lua
-- KEYS[1] = job key (rq:job:{job_id})
-- ARGV[4+] = field1, value1, field2, value2, ... for HSET
```

Patterns worth copying:
- **Variadic ARGV tail**: `redis.call("HSET", KEYS[1], unpack(ARGV, 4))` — fixed ARGV prefix is the
  parameter block, the tail is payload; `unpack(t, i)` avoids a Lua-side loop.
- **Integer status codes as the error protocol**: `0` = duplicate, `1` = success; `1/2/0` =
  acquired/refreshed/taken. No `redis.error_reply`, no exceptions on the hot path. Caller maps codes to
  domain outcomes (`DuplicateJobError`, `'acquired'|'refreshed'|'taken'`).
- **Sentinel instead of nil for "absent"**: TTL `-1` means "no TTL", so ARGV arity is constant.
- **Script composition by concatenation** (`RELEASE_AND_ENQUEUE = "redis.call('ZREM',KEYS[1],ARGV[4])\n" + ACQUIRE_AND_ENQUEUE`):
  cheap reuse, but it changes the SHA and silently shifts ARGV meaning — prefer Bull's `@include`.
- Rationale comments justifying Lua at all: "A plain GET + DEL sequence is racy: the lock can change
  owners between the two commands, deleting another owner's lock."

### 3. RQ — `rq/rate_limit.py` (10,686 stars) — **the anti-pattern exhibit**
https://github.com/rq/rq/blob/master/rq/rate_limit.py

Two declared keys, but the script then touches **many undeclared keys built by string concatenation**:

```lua
local origin = redis.call('HGET', 'rq:job:' .. job_id, 'origin')
redis.call('RPUSH', 'rq:queue:' .. origin, job_id)
```

On Dragonfly this needs `allow-undeclared-keys`, which forces the **global** transaction mode (whole-server
lock, no single-shard fast path). Also an **unbounded `while true` ZPOPMIN loop** over stale entries — on
Dragonfly a long script pins its thread and there is no `SCRIPT KILL` (repo issue #8269). Correct rewrite:
hoist the `HGET` to the caller and pass `rq:job:<id>`/`rq:queue:<origin>` in `KEYS`, with the whole group
under one `{tag}`; bound the loop with a max-iterations ARGV.

### 4. Bull — `lib/commands/*.lua` + `script-loader.js` (16,250 stars)
https://github.com/OptimalBits/bull/tree/develop/lib/commands

- **numKeys encoded in the filename**: `moveToFinished-9.lua`, `addJob-6.lua`, `removeJob-11.lua` — the key
  count is a greppable part of the script's identity, and the loader registers the command with that arity
  so a call site cannot pass the wrong `numkeys`.
- **A real preprocessor for shared Lua**: `--@include "<path>"` resolved by `ScriptLoader` (`IncludeRegex`),
  with an include stack in `ScriptLoaderError` and `EmptyLineRegex` stripping to shrink the shipped body —
  the maintainable answer to "a script can't call another script".

### 5. go-redis/redis_rate — GCRA limiter (1,050 stars)
https://github.com/go-redis/redis_rate

One declared key, everything else in ARGV. Uses **server-side time** rather than a client timestamp:

```lua
local now = redis.call("TIME")
now = (now[1] - jan_1_2017) + (now[2] / 1000000)
```

The epoch offset keeps the value in a range where float precision is adequate. Returns
`{allowed, remaining, retry_after, reset_after}` — one round trip yields the decision plus the back-off
hint. Caveat: `redis.call("TIME")` is non-deterministic, historically requiring `redis.replicate_commands()`.

### 6. node-rate-limiter-flexible (3,585 stars)
https://github.com/animir/node-rate-limiter-flexible

One key, ~8 lines, `SET NX` + `INCRBY` + `PTTL` with a defensive TTL re-arm:

```js
redis.call('set', KEYS[1], 0, 'EX', ARGV[2], 'NX') local consumed = redis.call('incrby', KEYS[1], ARGV[1])
```

Returns `{consumed, ttl}`. Registered via ioredis `defineCommand`, so NOSCRIPT recovery is the client's job.
The "insurance limiter" fallback is a JS-level circuit breaker on connection errors — **degradation policy
lives outside the script**, the right boundary.

### 7. bsm/redislock (1,767 stars)
https://github.com/bsm/redislock

Four small `.lua` files (`obtain`, `release`, `refresh`, `pttl`), **variadic KEYS** (`#KEYS`, plus an
optional fencing key), token compared by value:

```lua
local values = redis.call("mget", unpack(KEYS))
for i, _ in ipairs(KEYS) do if values[i] ~= ARGV[1] then return false end end
```

Cautionary detail: the four scripts use **three different reply shapes** (`false`, `redis.status_reply("OK")`,
raw integer). A skill should mandate one reply convention per script family.

### 8. predis/predis — `ScriptCommand` (7,779 stars)
https://github.com/predis/predis/blob/main/src/Command/ScriptCommand.php

The generic EVALSHA→EVAL fallback as a type, not as call-site code: `getId()` is hardcoded to `'EVALSHA'`,
`getKeysCount()` declares the arity (negative = "all but the last N args are keys"), and

```php
public function getEvalCommand() { $arguments[0] = $this->getScript(); return new RawCommand('EVAL', $arguments); }
```

builds the retry command by swapping the SHA for the body — same args, same arity.

### 9. celery/kombu (3,142 stars) — **effectively rejected**
https://github.com/celery/kombu/blob/master/kombu/transport/redis.py

No app-authored Lua in kombu's tree: `Mutex()` delegates to redis-py `client.lock(...)`, whose Lua lives in
redis-py (row 26). Negative result only — mature projects increasingly *delegate* locking Lua to the client
library. Treat the SOURCES row as `rejected: no Lua of its own`.

## B. Client libraries — script identity, NOSCRIPT, batching

### 10. go-redis — `script.go` (22,231 stars)
https://github.com/redis/go-redis/blob/master/script.go

- `Run` = optimistic `EVALSHA`, fall back to `EVAL` on NOSCRIPT. One extra round trip at most, once.
- `NewScriptServerSHA` obtains the digest from `SCRIPT LOAD` instead of hashing client-side — needed under
  **FIPS**, where a client-side SHA-1 call panics. Relevant to Dragonfly deployments in regulated envs.
- `isNoScriptErr` accepts **both** the typed error and the raw `NOSCRIPT` prefix, because on the
  "deferred autopipeline face the raw error reaches here untouched" — i.e. NOSCRIPT detection must survive
  pipelining.

### 11. rueidis — `lua.go` (2,980 stars)
https://github.com/redis/rueidis/blob/main/lua.go

Two decision-changing ideas:
- **Retryability is a property of the script, not of the client**: separate constructors
  `NewLuaScript` / `NewLuaScriptReadOnly` / `NewLuaScriptNoSha` / `NewLuaScriptRetryable`. Only a script the
  author declares idempotent gets `ToRetryable()`.
- **`ExecMulti` = the batching primitive**: `SCRIPT LOAD` to every node in parallel, then a single
  `DoMulti` pipeline of `EVALSHA` commands. "Cross-slot keys within the single LuaExec are prohibited"
  — each invocation stays single-slot, batching happens at the pipeline layer, not inside the script.
  This is exactly the shape Dragonfly wants: many small single-shard scripts, pipelined.
- `NewLuaScriptNoSha` exists "to fully avoid hash collision concerns" — EVAL-always is a legitimate mode.

### 12. redis-rs — `redis/src/script.rs` (4,254 stars)
https://github.com/redis-rs/redis-rs/blob/main/redis/src/script.rs

NOSCRIPT is a **typed** condition, not a string match:
`if err.kind() == ErrorKind::Server(crate::ServerErrorKind::NoScript) { self.load(con)?; eval_cmd.query(con) }`.
Retry is exactly once, and only for that error kind.

### 13. node-redis — `lua-script.ts` (17,582 stars)
https://github.com/redis/node-redis/blob/master/packages/client/lib/lua-script.ts

`RedisScriptConfig` carries `SCRIPT` **and** `NUMBER_OF_KEYS`, and `defineScript` computes `SHA1` at
definition time. Script + arity + digest are one immutable object created once at module load — the
cheapest correct form of "script versioning" for a single deployment.

### 14. Lettuce — `ExceptionFactory.java` (5,780 stars)
https://github.com/redis/lettuce/blob/main/src/main/java/io/lettuce/core/internal/ExceptionFactory.java

Server error prefixes are mapped to distinct types: `NOSCRIPT` → `RedisNoScriptException`, `BUSY` →
`RedisBusyException`, `LOADING`, `READONLY`. Relevant asymmetry: on Dragonfly v1.34.0 **`BUSY` never
arrives** (no script timeout, no `SCRIPT KILL`), so any client retry logic keyed on `RedisBusyException`
is dead code there and a runaway script is invisible to it.

## C. Official docs of Redis-compatible servers

### 15. Valkey — Scripting with Lua (27,207 stars)
https://valkey.io/topics/eval-intro/

- "all names of keys that a script accesses must be explicitly provided as input key arguments" — and keys
  must not be generated from database contents (exactly what `rq/rate_limit.py` does).
- "The Valkey script cache is always volatile ... and is not persisted." → NOSCRIPT is expected.
- **In pipelined contexts use plain `EVAL`, not `EVALSHA`**, because NOSCRIPT cannot be handled mid-pipeline
  when other clients' commands interleave. This contradicts the naive "always EVALSHA" rule and matters on
  Dragonfly, where pipelining + squashing is the main throughput lever.
- "While executing the script, all server activities are blocked during its entire runtime."

### 16. Valkey — Functions intro
https://valkey.io/topics/functions-intro/

Rationale for versioned, named libraries: "Functions are persisted to the AOF file and replicated from
primary to replicas, so they are as durable as the data itself", and "Because they are ephemeral, a script
can't call another script." Dragonfly has **no** `FUNCTION`/`FCALL`, so recover both client-side: an
`@include`-style bundler (Bull) plus explicit `SCRIPT LOAD` at startup.

### 17. Redis — Transactions
https://redis.io/docs/latest/develop/using-commands/transactions/

"Redis does not support rollbacks of transactions", and the closing verdict: "Everything you can do with a
Redis Transaction, you can also do with a script, and usually the script will be both simpler and faster."
Also the `WATCH`/CAS ZPOP example — the retry-loop that a script replaces outright.

### 18. Redis — Keys and values (key layout)
https://redis.io/docs/latest/develop/using-commands/keyspace/

- Schema: "Try to stick with a schema. For instance `object-type:id` is a good idea".
- Long keys cost twice: "a key of 1024 bytes is a bad idea not only memory-wise, but also because the
  lookup of the key in the dataset may require several costly key-comparisons."
- Short keys are also discouraged (`u1000flw` vs `user:1000:followers`).
- **Hashtag warning, in direct tension with Dragonfly**: "you shouldn't make a habit of using them
  generally. If you have too many keys mapped to the same hash slot then this will eventually harm the
  performance of your database." Dragonfly's `--lock_on_hashtags` *wants* co-tagged groups; the skill must
  state the Dragonfly-specific inversion and the cardinality caveat (tag per queue/tenant, never one global tag).

### 19. Redis — Diagnosing latency issues
https://redis.io/docs/latest/operate/oss_and_stack/management/optimization/latency/

The explicit decision ladder for "should this be Lua at all":
> Prefer to use aggregated commands (MSET/MGET), or commands with variadic parameters (if possible) over
> pipelining. Prefer to use pipelining (if possible) over sequence of roundtrips. Redis supports Lua
> server-side scripting to cover cases that are not suitable for raw pipelining (for instance when the
> result of a command is an input for the following commands).

Lua is the **last** rung, justified by data dependency, not by round-trip count. Also: slow commands block
everyone; use SLOWLOG and the latency monitor; many keys expiring in the same second is itself a stall.

### 20. Apache Kvrocks — Supported commands (4,431 stars)
https://kvrocks.apache.org/docs/supported-commands

`EVAL`/`EVALSHA` (v2.0.4), `EVAL_RO`/`EVALSHA_RO` (v2.2.0), `SCRIPT`, plus `FCALL`/`FCALL_RO` (v2.7.0);
`SCRIPT KILL`/`DEBUG` unsupported. Kvrocks has Functions, Dragonfly does not — "Redis-compatible" cannot be
assumed to cover the programmability surface.

### 21. Garnet — Scripting and functions (12,021 stars)
https://microsoft.github.io/garnet/docs/commands/scripting

Supports `EVAL`, `EVALSHA`, `SCRIPT LOAD|EXISTS|FLUSH`; **no** `FUNCTION`. Enabled with a `--lua` switch.
No documented KEYS-declaration constraint, no documented timeout or memory limit.

### 22. Garnet PR #882 — Lua allocation/performance work (12,021 stars)
https://github.com/microsoft/garnet/pull/882

The only hard *measured* data found on where Lua time actually goes in a modern Redis-compatible server:
- "the `redis.call` case is **~8x faster**"; `Script4` went 1,822 ns → 229 ns, allocations 776 B → 0 B.
- "**SHA lookups are on the fast path for script execution**" — script-cache lookup is itself hot; keep the
  set of distinct scripts small and stable.
- "Because quite a lot of the overhead here is in the **PInvoke transition**, some more work is moved into
  Lua." Generalizes to Dragonfly: each `redis.call` is a host↔Lua boundary crossing, so prefer a few
  variadic calls (`HSET k unpack(ARGV,4)`, `MGET unpack(KEYS)`) over per-element call loops.
- Admitted gap: "we need memory tracking, memory limits, and script timeouts" — same class as Dragonfly's
  missing `SCRIPT KILL`.

### 23. Lua 5.4 Reference Manual (lua.org)
https://www.lua.org/manual/5.4/manual.html

- "The type *number* represents both integer numbers and real (floating-point) numbers, using two subtypes:
  *integer* and *float*." Redis-lineage servers convert a Lua **number reply to an integer by truncation**,
  so returning `3.7` yields `3` — return strings for anything fractional.
- "The conversion from float to integer checks whether the float has an exact representation as an integer."
- `//` is floor division "that rounds the quotient towards minus infinity" — the correct operator for
  bucket/window arithmetic in rate limiters (plain `/` always yields a float in 5.4).
- `..` coerces numbers "in a non-specified format" — never build a reply payload with `..` on a number;
  use `string.format` or `tostring` deliberately.

## Cross-cutting conclusions for the skill

1. **Declare every key; never concatenate a key inside the script.** Universal in docs (Valkey), violated in
   the wild (RQ rate limiter) — and on Dragonfly the violation costs the single-shard fast path.
2. **Pin the key count to the script's identity** (Bull's `-N.lua` filename, node-redis `NUMBER_OF_KEYS`,
   predis `getKeysCount()`).
3. **One reply convention per script family** — integer status codes (RQ) or a fixed result tuple
   (redis_rate); never mix `false`/`status_reply`/int like bsm/redislock.
4. **NOSCRIPT is normal**: cache SHA, detect by typed error *and* raw prefix, reload, retry exactly once —
   but use plain `EVAL` inside pipelines.
5. **Batch outside the script, not inside it**: rueidis `ExecMulti`; Sidekiq shows batch size is a
   durability decision.
6. **Minimize host↔Lua crossings inside the script**: variadic `unpack` calls beat per-element loops (Garnet
   measurement + RQ/redislock practice).
7. **Bound every loop**; there is no `SCRIPT KILL` on Dragonfly.
8. **Lua is the last rung** of the latency ladder: variadic → pipeline → Lua, justified by data dependency.
9. **Hashtags are the Dragonfly inversion**: Redis says don't habitually co-tag, Dragonfly's
   `--lock_on_hashtags` requires it — tag per queue/tenant, and watch tag cardinality.
10. **No FUNCTION on Dragonfly** → rebuild versioning/reuse client-side with an `@include` bundler and
    startup `SCRIPT LOAD`.

## Gaps not filled

- No public benchmark of EVALSHA throughput **on Dragonfly specifically** beyond the BullMQ blog posts
  (already rows 6–7); Garnet PR #882 is the closest measured proxy and is a different engine.
- No high-star project found that **tests** Lua as a first-class practice (no Lua test harness, golden-reply
  suite, or script-level property tests in any of the 9 libraries read); testing guidance must be derived.
- No official Dragonfly or Valkey page stating a recommended **maximum script body size** or maximum
  `numkeys`; only Redis's `LUAI_MAXCSTACK` (row 23) bounds `unpack`.
- `redis_exporter`, Redisson and Jedis were not read (context budget); Redisson's `evalsha` batching remains
  an open lead.