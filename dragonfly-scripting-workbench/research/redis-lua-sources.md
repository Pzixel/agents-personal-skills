# Redis/Dragonfly Lua scripting — sourced facts

Scope: server-side Lua (EVAL/EVALSHA) for Dragonfly v1.34.0, a Redis-compatible server. Every fact = claim + URL + verbatim quote or code location.
Flags: **[R]** Redis-only (verified against Redis docs/source, may differ on Dragonfly) · **[S]** likely-shared (protocol/Lua-semantics level) · **[DF]** Dragonfly-verified.

Star counts verified 2026-09-16 via `api.github.com`: taskforcesh/bullmq **9,403** · redis/memtier_benchmark **1,051** · redis/redis-py **13,642** · redis/ioredis **15,338** · dragonflydb/dragonfly **31,532**. All ≥500.

## Facts that change how to write scripts

1. **Declaring every key in KEYS is a performance contract on Dragonfly, not just hygiene.** Undeclared-key access is rejected by default, and enabling it "locks the entire data store for each Lua script execution." — *Dragonfly-verified* (§6.5)
2. **Dragonfly runs Lua 5.4; Redis runs Lua 5.1.** Every 5.1-specific idiom (`unpack` as a global, the `bit` library, no integer subtype) needs re-verification. — *Dragonfly-verified* (§6.3)
3. **Dragonfly's flag directive is `--!df flags=...`, not `#!lua flags=...`.** A Redis shebang will not configure a Dragonfly script. — *Dragonfly-verified* (§6.4)
4. **Only `allow-undeclared-keys` and `disable-atomicity` do anything on Dragonfly; `no-writes` is parsed and ignored.** `allow-oom`, `allow-stale`, `no-cluster`, `allow-cross-slot-keys` do not exist there. — *Dragonfly-verified* (§6.4)
5. **Redis Functions (FUNCTION LOAD / FCALL) do not work on Dragonfly v1.34.0.** Use EVAL/EVALSHA. — *Dragonfly-verified* (§6.1)
6. **`SCRIPT KILL` and `SCRIPT DEBUG` are unsupported on Dragonfly.** No recovery path for a runaway script; keep scripts provably bounded. — *Dragonfly-verified* (§6.2)
7. **A Lua array reply truncates at the first `nil`, floats truncate to integers, and string-keyed fields are dropped silently.** `{1,2,3.3333,somekey='x','foo',nil,'bar'}` returns four elements. Return floats as strings. — *likely-shared* (§1.3)
8. **A missing key arrives in Lua as `false`, not `nil`** (RESP2 null → Lua false). Test `== false`. Under `redis.setresp(3)` it becomes `nil`. — *likely-shared* (§1.3–1.4)
9. **`redis.call` aborts the script and returns the raw error to the client; `redis.pcall` returns `{err=...}` for you to handle.** Choose deliberately per call site. — *likely-shared* (§1.2)
10. **Globals are a hard error — every variable and function must be `local`.** Cross-invocation state must live in the keyspace. — *likely-shared* (§1.7)
11. **The script cache is volatile and, on Redis 7.4+, LRU-evicted while running.** Never assume "loaded at startup" means "still loaded"; always keep NOSCRIPT recovery. — *Redis-only* (§1.8)
12. **Inside a pipeline, NOSCRIPT is unrecoverable** — redis.io says so explicitly. redis-py pre-loads via `SCRIPT EXISTS` before `execute()`; ioredis rewrites the first send per socket to EVAL. — *likely-shared* (§1.8, §5)
13. **Don't `unpack()` an unbounded table into `redis.call`.** On Redis it dies at `LUAI_MAXCSTACK`=8000 with `too many results to unpack`; Dragonfly's ceiling is unmeasured. Chunk it. — *Redis-only* limit, shared hazard (§1.11, §6.3)
14. **Alias `local rcall = redis.call` at the top of every script** — the convention 47 of BullMQ's 49 scripts follow, and mandatory if you splice shared fragments that call `rcall`. — *likely-shared* (§3.4)
15. **Benchmark with `memtier_benchmark --command="EVALSHA <sha> 1 __key__ __data__"` or `dfly_bench --command`; Dragonfly's own docs say `redis-benchmark` becomes the bottleneck.** `dfly_bench --command` silently ignores `--json_out_file`. — *Dragonfly-verified* (§4)

## 1. redis.io "Scripting with Lua" and "EVAL"

Primary URLs: `eval-intro` = https://redis.io/docs/latest/develop/programmability/eval-intro/ · `lua-api` = https://redis.io/docs/latest/develop/programmability/lua-api/ · `prog` = https://redis.io/docs/latest/develop/programmability/ · `eval` = https://redis.io/docs/latest/commands/eval/

### 1.1 Script flags (shebang) — **[R]**, superseded on Dragonfly by §6.4
- Declared on line 1 via shebang. `eval-intro` — `#!lua flags=no-writes,allow-stale` / `local x = redis.call('get','x')`
- Adding `#!` at all changes defaults. `eval-intro` — "as soon as Redis sees the `#!` comment, it'll treat the script as if it declares flags, even if no flags are defined, it still has a different set of defaults"
- Shebang scripts lose cross-slot access. `eval-intro` — "scripts without `#!` can run commands that access keys belonging to different cluster hash slots, but ones with `#!` inherit the default flags, so they cannot."
- Defaults absent flags: read+write, single-slot, denied on stale replica, denied under OOM. `lua-api#script_flags` — "1. They can read and write data. 2. They can run in cluster mode, and are not able to run commands accessing keys of different hash slots. 3. Execution against a stale replica is denied... 4. Execution under low memory is denied"
- `no-writes` — "this flag indicates that the script only reads data but never writes." Enables `EVAL_RO`, replica execution, execution under disk error, and implies allow-oom: "4. When over the memory limit since it implies the script doesn't increase memory consumption"
- `no-writes` still errors at runtime on a write; PUBLISH/SPUBLISH/PFCOUNT count as writes. `lua-api` — "the server will return an error if the script attempts to call a write command. Also note that currently PUBLISH, SPUBLISH and PFCOUNT are also considered write commands in scripts"
- `allow-oom` — "use this flag to allow a script to execute when the server is out of memory (OOM)." Also unblocks commands normally banned in that state.
- `allow-stale` — runs on stale replica when `replica-serve-stale-data no`, but "the script will still be unable to execute any command that accesses stale data."
- `no-cluster` — "the flag causes the script to return an error in Redis cluster mode."
- `allow-cross-slot-keys` — "Declared keys to the script are still always required to hash to a single slot." and "This flag has no effect when cluster mode is disabled."

### 1.2 redis.call vs redis.pcall, errors — **[S]**
- `eval-intro` — "Errors raised from calling `redis.call()` function are returned directly to the client that had executed it. Conversely, errors encountered when calling the `redis.pcall()` function are returned to the script's execution context instead"
- `lua-api#redis.pcall` — "Always returns a reply." / "Never throws a runtime exception, and returns in its stead a `redis.error_reply`"
- Detection idiom, `lua-api` — `local reply = redis.pcall('ECHO', unpack(ARGV))` / `if reply['err'] ~= nil then`
- `redis.error_reply(x)` ≡ `{err=x}`; `redis.status_reply(x)` ≡ `{ok=x}`. `lua-api#redis.error_reply` — `local reply1 = { err = text }` / `local reply2 = redis.error_reply(text)`
- `lua-api` — "By convention, Redis uses the first word of an error string as a unique error code for specific errors or `ERR` for general-purpose errors."

### 1.3 RESP conversion rules — **[S]**, all from `lua-api#data-type-conversion`
- "Lua number -> RESP2 integer reply (the number is converted into an integer)" and "**If you want to return a Lua float, it should be returned as a string**"
- "Lua table (indexed, non-associative array) -> RESP2 array reply (truncated at the first Lua `nil` value encountered in the table, if any)"
- "When a Lua table is an associative array that contains keys and their respective values, the converted Redis reply will **not** include them."
- All three traps at once: `EVAL "return { 1, 2, 3.3333, somekey = 'somevalue', 'foo', nil , 'bar' }" 0` → `1) (integer) 1  2) (integer) 2  3) (integer) 3  4) "foo"`
- "Lua boolean false -> RESP2 null bulk reply" and "Lua Boolean `true` -> RESP2 integer reply with value of 1."
- Inbound: "RESP2 null bulk reply and RESP2 null multi-bulk reply -> Lua false boolean type" → a missing key is `false`, never `nil`.
- "RESP2 status reply -> Lua table with a single _ok_ field containing the status string"; error reply → `{err=...}` table.

### 1.4 redis.setresp — **[R]** (since 6.0; verify on Dragonfly)
- `lua-api#redis.setresp` — "It expects a single numerical argument as the protocol's version. The default protocol version is _2_, but it can be switched to version _3_."
- Under RESP3: maps → `{map=...}`, sets → `{set=...}`, doubles → `{double=...}`, and "RESP3 null -> Lua `nil`." (not `false`).
- Return conversion follows the client's HELLO, independent of `setresp`: "Type conversion from a script's returned Lua data type depends on the user's choice of protocol (see the HELLO command)."
- "presently, RESP3's attributes, streamed strings and streamed aggregated data types are not supported by the Redis Lua API."

### 1.5 redis.sha1hex, redis.log — **[S]**
- `lua-api#redis.sha1hex` — `EVAL "return redis.sha1hex('')" 0` → `"da39a3ee5e6b4b0d3255bfef95601890afd80709"`
- `lua-api#redis.log` — levels `redis.LOG_DEBUG|LOG_VERBOSE|LOG_NOTICE|LOG_WARNING`; "The log only records messages equal or greater in level than the server's `loglevel` configuration directive."

### 1.6 Libraries — `lua-api#runtime-libraries`
- Standard: `string`, `table`, `math`, and (7.4+) `os` — but "currently only the following os functions is exposed: `os.clock()`"
- External: `struct`, `cjson`, `cmsgpack`, `bit` (bitop, 2.8.18+). Example: `EVAL "return cjson.encode({ ['foo'] = 'bar' })" 0` → `"{\"foo\":\"bar\"}"`
- No modules: "The sandboxed execution context prevents the loading modules by disabling Lua's `require` function."
- Engine version, `prog` — "Presently, Redis supports a single scripting engine, the Lua 5.1 interpreter." **[R]** — Dragonfly uses 5.4, see §6.3.

### 1.7 Global variable protection — **[S]** in spirit, exact message **[R]**
- `lua-api#global-variables-and-functions` — "Redis will return a \"Script attempted to create global variable 'my_global_variable\" error" and "all variable and function definitions are required to be declared as local."
- "In the (somewhat uncommon) use case that a context needs to be maintain between executions, you should store the context in Redis' keyspace."

### 1.8 Script cache semantics
- `eval-intro` — "The cache's contents are organized by the scripts' SHA1 digest sums, so the SHA1 digest sum of a script uniquely identifies it in the cache."
- `SCRIPT LOAD` compiles without executing: "The server doesn't execute the script, but instead just compiles and loads it to the server's cache." → `SCRIPT LOAD "return 'Immabe a cached script'"` returns `"c664a3bf70bd1d45c4284ffebb65a6f2299bfc9f"`
- **[S]** "The Redis script cache is **always volatile**. It isn't considered as a part of the database and is **not persisted**."
- Missing SHA1: `EVALSHA ffffffffffffffffffffffffffffffffffffffff 0` → `(error) NOSCRIPT No matching script`
- `SCRIPT FLUSH` — "Running the command will _completely flush_ the scripts cache, removing all the scripts executed so far." `SCRIPT EXISTS` probes it.
- **[R]** `eval` — "Starting with Redis 7.4, Redis evicts scripts loaded with `EVAL` or `EVAL_RO` from the script cache when the cache reaches a certain size. Redis evicts the least recently used scripts first." → NOSCRIPT can occur mid-run without a restart.
- `eval-intro` — "Because of that, the `NOSCRIPT` error can return from a pipelined request but can't be handled." and "a client library's implementation should revert to using plain `EVAL` of parameterized in the context of a pipeline."
- Dynamic scripts are an anti-pattern. `eval` — "users will abuse Lua `EVAL` by embedding values in the script instead of providing them as arguments... These values are added to the Lua interpreter and cached in Redis, consuming a large amount of memory over time."

### 1.9 Effects replication — **[R]**
- `eval-intro` — "In Redis 5.0, effects replication became the default mode. As of Redis 7.0, verbatim replication is no longer supported."
- "the sequence of commands that the script generated are wrapped into a MULTI/EXEC transaction and are sent to the replicas and AOF."
- Non-determinism is therefore fine: "When script effects replication is enabled, the restrictions on non-deterministic functions are removed."
- `redis.set_repl(REPL_ALL|REPL_AOF|REPL_REPLICA|REPL_NONE)` — `lua-api#redis.set_repl` — "By default, the scripting engine is initialized to the `redis.REPL_ALL` setting when a script begins its execution."
- `redis.replicate_commands()` is deprecated as of 7.0 and always succeeds.

### 1.10 Blocking, busy-reply-threshold, BUSY, SCRIPT KILL — **[R]**, exactly where Dragonfly diverges (§6.5)
- `prog` — "The script's execution blocks all server activities during its entire time... The blocking semantics of an executed script apply to all connected clients at all times."
- "if you intend to use a slow script in your application, be aware that all other clients are blocked and can't execute any command while it is running."
- "Scripts are subject to a maximum execution time (set by default to five seconds)." and "The configuration parameter affecting max execution time is called `busy-reply-threshold`." (formerly `lua-time-limit`)
- Timeout does not kill: "It starts accepting commands again from other clients but will reply with a BUSY error to all the clients sending normal commands. The only commands allowed in this state are SCRIPT KILL, FUNCTION KILL, and SHUTDOWN NOSAVE."
- "If the script had already performed even a single write operation, the only command allowed is `SHUTDOWN NOSAVE`"
- Read-only scripts: "They can always be killed by the SCRIPT KILL command." / "They never fail with OOM error when redis is over the memory limit."

### 1.11 Argument-count limits
- **[R]** `unpack()` is bounded by the Lua C stack. https://github.com/redis/redis/blob/unstable/deps/lua/src/luaconf.h:446 — `#define LUAI_MAXCSTACK	8000` / "LUAI_MAXCSTACK limits the number of Lua stack slots that a C function can use."
- Overflow raises from `luaB_unpack`. https://github.com/redis/redis/blob/unstable/deps/lua/src/lbaselib.c:350-351 — `if (n >= INT_MAX || !lua_checkstack(L, ++n))` / `return luaL_error(L, "too many results to unpack");`
- Practical rule: never `redis.call('RPUSH', k, unpack(t))` with unbounded `t`; chunk at ~1000–5000. Same applies to `unpack(ARGV)`.
- **[R]** Wire protocol bounds in `processMultibulkBuffer`. https://github.com/redis/redis/blob/unstable/src/networking.c:3725-3733 — `addReplyError(c,"Protocol error: invalid bulk length");` / `addReplyError(c,"Protocol error: invalid multibulk length");`. Bulk ceiling is `server.proto_max_bulk_len` (`proto-max-bulk-len`, networking.c:3477).
- `eval` — `"syntax_fmt": "EVAL script numkeys [key [key ...]] [arg [arg ...]]"`, `"arity": -3`
- **[S]**, load-bearing on Dragonfly — `eval` — "all keys that a script accesses must be explicitly provided as input key arguments. Scripts should never access keys with programmatically-generated names or based on the contents of data structures stored in the database."

## 2. Performance guidance from official sources

- **No official source quantifies per-`redis.call` cost.** What is verifiable is the mechanism. **[R]** Each call converts Lua stack values into a Redis `robj*` argv with a small reuse cache. https://github.com/redis/redis/blob/unstable/src/script_lua.c `luaArgsToRedisArgv` (L778-830), `freeLuaRedisArgv` (L849+) — `/* Cache of recently used small arguments to avoid malloc calls. */` / `static robj *lua_args_cached_objects[LUA_CMD_OBJCACHE_SIZE];`
- That cache covers only the first 32 args, each ≤64 bytes. https://github.com/redis/redis/blob/unstable/src/server.h:4503-4504 — `#define LUA_CMD_OBJCACHE_SIZE 32` / `#define LUA_CMD_OBJCACHE_MAX_LEN 64` → a call with >32 args or args >64 B falls off the malloc-avoidance path. Argues for *moderately* wide multi-field calls, not maximally wide ones. Redis internal — measure on Dragonfly.
- The syscall cost that makes client-side pipelining a 10× win does **not** exist inside a script. https://redis.io/docs/latest/develop/using-commands/pipelining/ — "serving each command is very cheap from the point of view of accessing the data structures and producing the reply, but it is very costly from the point of view of doing the socket I/O. This involves calling the `read()` and `write()` syscall... The context switch is a huge speed penalty." Plus `prog` — "It is not hard to create fast scripts because scripting's overhead is very low."
  - Consequence: batching `HMGET`/`HSET` multi-field/`ZADD` multi-member/`MGET` over a loop of `redis.call`s still helps (fewer conversions + lookups), but the payoff is far smaller than client-side batching. Quantify locally.
- Scripts beat pipelining for read-compute-write. https://redis.io/docs/latest/develop/using-commands/pipelining/#pipelining-vs-scripting — "A big advantage of scripting is that it is able to both read and write data with minimal latency, making operations like *read, compute, write* very fast (pipelining can't help in this scenario since the client needs the reply of the read command before it can call the write command)."
- Keep scripts short — they block, and slow scripts starve eviction. `eval-intro#execution-under-low-memory-conditions` — "In addition, Lua scripts should be as fast as possible so that eviction can kick in between executions."
- **[R]** OOM is checked at the first memory-consuming write, then the rest runs regardless. Same URL — "the first write command encountered in the script that uses additional memory will cause the script to abort (unless `redis.pcall` was used)." / "If subsequent writes in the script consume additional memory, Redis' memory usage can exceed the threshold set by the `maxmemory` configuration directive."
- `SCRIPT LOAD` makes pipelined EVALSHA safe. Pipelining doc — "Sometimes the application may also want to send EVAL or EVALSHA commands in a pipeline. This is entirely possible and Redis explicitly supports it with the SCRIPT LOAD command (it guarantees that EVALSHA can be called without the risk of failing)."
- Batch pipelines (~10k) to bound reply memory. Same URL — "it is better to send them as batches each containing a reasonable number, for instance 10k commands, read the replies, and then send another 10k commands again"
- **[S]** Hash tags colocate keys. https://redis.io/docs/latest/operate/oss_and_stack/reference/cluster-spec/#hash-tags — "The two keys `{user1000}.following` and `{user1000}.followers` will hash to the same hash slot since only the substring `user1000` will be hashed". Rules: "IF the key contains a `{` character. AND IF there is a `}` character to the right of `{`. AND IF there are one or more characters between the first occurrence of `{` and the first occurrence of `}`." Edge cases: `foo{}{bar}` hashes whole; `foo{{bar}}zap` hashes `{bar`; `foo{bar}{zap}` hashes `bar`. Dragonfly reuses this for `--lock_on_hashtags` (§6.6).
- Avoid `KEYS`-pattern scans inside scripts — the key-declaration rule forbids it outright. `eval` — "Scripts should never access keys with programmatically-generated names or based on the contents of data structures stored in the database."
- **UNVERIFIED from an allowed source:** `t[#t+1]` vs `table.insert`, and `table.concat` for string building. Neither redis.io nor redis/redis states a preference; these are Lua-community idioms. Present them as conventions, or measure. (BullMQ's real style is in §3.)
- **UNVERIFIED from an allowed source:** cjson encode/decode cost. redis.io only says the library "provides fast JSON encoding and decoding" (`lua-api#cjson-library`). "Avoid cjson in hot paths" is a hypothesis to benchmark, not a sourced fact.

## 3. BullMQ — how a production codebase structures Lua

https://github.com/taskforcesh/bullmq — **9,403 stars** (`"stargazers_count": 9403`, 2026-09-16).

### 3.1 Layout
- 49 top-level `.lua` in `src/commands/`, 66 fragments in `src/commands/includes/`. https://github.com/taskforcesh/bullmq/tree/master/src/commands
- Scripts: `addStandardJob-9.lua`, `addDelayedJob-6.lua`, `addPrioritizedJob-9.lua`, `moveToActive-11.lua`, `moveToFinished-14.lua`, `moveToDelayed-11.lua`, `moveStalledJobsToWait-9.lua`, `obliterate-2.lua`, `retryJob-11.lua`, `getCounts-1.lua`, `extendLock-2.lua`, `promote-9.lua`.
- Includes: `addJobInTargetList.lua`, `getDelayedScore.lua`, `getPriorityScore.lua`, `promoteDelayedJobs.lua`, `prepareJobForProcessing.lua`, `removeJob.lua`, `trimEvents.lua`, `isQueuePausedOrMaxed.lua`, `getRateLimitTTL.lua`, `batches.lua`, `storeJob.lua`, `destructureJobKey.lua`.
- Only non-Lua files there: `script-loader.ts`, `index.ts`.

### 3.2 `-N` filename suffix = numberOfKeys
- `src/commands/script-loader.ts:591-599` (`splitFilename`) — `const [name, num] = longName.split('-'); const numberOfKeys = num ? parseInt(num, 10) : undefined;`
- So `moveToFinished-14.lua` declares 14 keys, `getCounts-1.lua` declares 1. Worth copying: a KEYS-arity change becomes a rename, not a silent runtime bug.

### 3.3 `--@include` bundling
- Directive is a Lua comment; `--@include` and `--- @include` both parse. `script-loader.ts:11` — `const IncludeRegex = /^[-]{2,3}[ \t]*@include[ \t]+(["'])(.+?)\1[; \t\n]*$/m;`
- Real usage. https://raw.githubusercontent.com/taskforcesh/bullmq/master/src/commands/moveToActive-11.lua:46-48 — `-- Includes` / `--- @include "includes/getQueueMetadata"`
- Transitive, with paths relative to the includes dir. `src/commands/includes/removeJob.lua:5-8` — `--- @include "removeDeduplicationKeyIfNeededOnRemoval"`
- Each include is emitted exactly once: a SHA1 placeholder is substituted on first occurrence, duplicates blanked. `script-loader.ts:650-652` (`getPathHash`) — ``return `@@${sha1(normalizedPath)}`;`` — and `:383-406` (`interpolate`). Re-including the same file in one script errors: `file "X" already included in "Y"` (~L286).
- Output registers as an ioredis custom command. `script-loader.ts:14-21`, `:566` — `client.defineCommand(command.name, command.options)` with `{ name, options: { numberOfKeys, includes, lua } }`.
- Net: the bundler is a **build step**, not a runtime `require` — Lua's sandbox has no module system (§1.6), so text splicing is the only option.

### 3.4 `rcall` local alias
- `moveToActive-11.lua:38` — `local rcall = redis.call`. Also `addStandardJob-9.lua:53`, `moveToFinished-14.lua:60`, `obliterate-2.lua:22`, `extendLock-2.lua:15`, `getCounts-1.lua:9`. **47 of 49** top-level scripts define it.
- Fragments **call** but don't define it — 59 includes call `rcall(`, only 1 declares it. `includes/removeJob.lua:14` — `local deduplicationId = rcall("HGET", jobKey, "deid")`. So the alias is a contract between bundler and fragment.
- **Rationale is UNVERIFIED as an explicit BullMQ/redis.io statement.** Mechanism is standard Lua: `redis.call` is a field lookup on the global `redis` table per invocation; a `local` becomes an upvalue resolved by register index. Treat as plausible-and-cheap, not a sourced number.

### 3.5 KEYS ordering discipline
- Header comment maps the whole KEYS array. https://raw.githubusercontent.com/taskforcesh/bullmq/master/src/commands/addStandardJob-9.lua:17-21 — `      Input:` / `      KEYS[1] 'wait',` / `      KEYS[2] 'paused'` / `      KEYS[3] 'meta'`
- Bind names once, then use names: `moveToActive-11.lua:39` — `local waitKey = KEYS[1]`. Raw `KEYS[n]` is passed into fragments: `moveToActive-11.lua:55` — `getQueueMetadata(KEYS[9], activeKey, waitKey)`
- Why one slot is required. https://docs.bullmq.io/bull/patterns/redis-cluster (`docs/gitbook/patterns/redis-cluster.md:7`) — "Bull internals require atomic operations that span different keys. This behavior breaks Redis's rules for cluster configurations… use a queue prefix inside brackets."

### 3.6 BullMQ on Dragonfly
- The page is https://docs.bullmq.io/guide/redis-tm-compatibility/dragonfly — **not** `going-to-production`, which has no Dragonfly section.
- Hashtag requirement, verbatim — "Primarily, you should name your queues using curly braces. This naming convention allows Dragonfly to assign a thread to each queue. For instance, if your queue is named myqueue, rename it to {myqueue}."
- Rationale — "If you manage multiple queues, this approach enables you to allocate different CPU cores to each queue, significantly enhancing performance."
- Documented limitation — "Be aware that certain features like priorities and rate-limiting might not function across multiple queues."
- The docs page does **not** list server flags; it links out to non-allowed domains, so that flag text is **UNVERIFIED**.
- The flags **are** verifiable in the repo. https://github.com/taskforcesh/bullmq/blob/master/docker-compose.yml:8-16 (image `dragonflydb/dragonfly:v1.40.2`) — `DFLY_cluster_mode: 'emulated'` / `DFLY_lock_on_hashtags: 'true'` / `DFLY_default_lua_flags: 'allow-undeclared-keys'`
- `DFLY_default_lua_flags: allow-undeclared-keys` is the key line: a **Dragonfly-only Lua flag with no Redis equivalent**, needed because BullMQ scripts touch keys not passed in KEYS. Cost is in §6.5.
- Exercised in CI, not aspirational: `.github/workflows/test.yml:318-334` (job `node-dragonflydb`, `DFLY_cluster_mode: emulated`, `DFLY_lock_on_hashtags: true`); same at `.github/workflows/python-test.yml:126`.

### 3.7 A real failure mode worth teaching
- Passing a Lua `nil`/boolean/float into `rcall`. https://docs.bullmq.io/guide/troubleshooting (`docs/gitbook/guide/troubleshooting.md:26`) — "This can cause BullMQ's internal Lua scripts to throw ERR Error running script ... Lua redis() command arguments must be strings or integers."
- Matches the Redis check at https://github.com/redis/redis/blob/unstable/src/script_lua.c:842 — `luaPushError(lua, "Lua redis lib command arguments must be strings or integers");`
- Script-size guidance in BullMQ docs: **UNVERIFIED** — no statement found on maximum or target Lua script size.

## 4. Benchmarking tooling

### 4.1 memtier_benchmark
- **The repo moved.** `github.com/RedisLabs/memtier_benchmark` 301-redirects to **https://github.com/redis/memtier_benchmark** (`api.github.com/repositories/11006053` → `"full_name": "redis/memtier_benchmark"`). **1,051 stars**, 252 forks, verified 2026-09-16.
- `--command` sends an arbitrary literal command. `memtier_benchmark.cpp:2717-2724` — `--command=COMMAND          Specify a command to send in quotes.` / `Each command that you specify is run with its ratio and key-pattern options.`
- Placeholders, same help block — `__key__: Use key generated from Key Options.` / `__data__: Use data generated from Object Options.`
- README example. https://raw.githubusercontent.com/redis/memtier_benchmark/master/README.md — `$ memtier_benchmark --command="SET foo __data__" --command="SET bar __data__" --command="GET foo"`
- `--command-ratio`, `:2725` — `The number of times the command is sent in sequence.(default: 1)`
- `--command-key-pattern`, `:2726-2731` — `Key pattern for the command (default: R):` — `G` Gaussian, `R` uniform Random, `Z` zipf, `S` Sequential, `P` Parallel.
- **EVALSHA works**: `--command` is textual RESP with no EVAL/EVALSHA special-casing (grep `evalsha` in `memtier_benchmark.cpp` → 0 hits); `__key__`/`__data__` substitute anywhere after the first token.
- **Hard gotcha:** the first token must be a literal command name. A placeholder there — matched by *substring*, so `FOO__key__` also trips — hits `first_arg_is_placeholder()` at `memtier_benchmark.cpp:1037-1055` (formerly aborting in `protocol.cpp` with `"first arg is not command name?"`). `--command="EVALSHA <sha> 1 __key__ __data__"` is fine.
- Docker image, README "Using Docker" — `$ docker run --rm redislabs/memtier_benchmark:latest --help` (image name is still `redislabs/…` though the repo moved to `redis/`).
- Flags verbatim, `memtier_benchmark.cpp:2608-2673` — `-n, --requests=NUMBER          Number of total requests per client (default: 10000)` · `-c, --clients=NUMBER           Number of clients per thread (default: 50)` · `-t, --threads=NUMBER           Number of threads (default: 4)` · `--test-time=SECS           Number of seconds to run the test` · `--ratio=RATIO              Set:Get ratio (default: 1:10)` · `--pipeline=NUMBER          Number of concurrent pipelined requests (default: 1)` · `--distinct-client-seed     Use a different random seed for each client` · `--hide-histogram           Don't print detailed latency histogram` · `--json-out-file=FILE       Name of JSON output file, if not set, will not print to json`
- **`-n` is per client**, unlike `redis-benchmark -n`. Total requests = `n × c × t`.

### 4.2 redis-benchmark
- Arbitrary trailing argv is sent verbatim as one command. https://github.com/redis/redis/blob/unstable/src/redis-benchmark.c:1861-1889 — `/* Run benchmark with command in the remainder of the arguments. */` / `if (argc) { ... redisFormatCommandArgv(&cmd,argc,(const char**)sds_args,argvlen); benchmark(title,cmd,len); }`
- Usage, `redis-benchmark.c:1590` — `Usage: redis-benchmark [OPTIONS] [COMMAND ARGS...]`
- **EVAL is documented; EVALSHA is not** (same generic path). `redis-benchmark.c:1651-1652` — ` Benchmark a specific command line:` / `   $ redis-benchmark -r 10000 -n 10000 eval 'return redis.call("ping")' 0`. Doc page https://redis.io/docs/latest/operate/oss_and_stack/management/optimization/benchmarks/ — `$ redis-benchmark -n 100000 -q script load "redis.call('set','foo','bar')"`. **EVALSHA support inferred from code, not doc-verified.**
- `--threads` exists (`redis-benchmark.c:1497`); docs: `--threads <num>: Enable multi-thread mode.` · `-P <numreq>: Pipeline <numreq> requests. Default 1 (no pipeline).`
- `-r` + `__rand_int__` is a blind textual scan, so it works inside EVAL/EVALSHA key args. `redis-benchmark.c:755-762` (`strstr(p,"__rand_int__")`) — "the benchmark will expand the string __rand_int__ inside an argument with a 12 digits number in the specified range from 0 to keyspacelen-1." and "Note: If -r is omitted, all commands in a benchmark will use the same key."
- **[DF]** Dragonfly advises against it. https://www.dragonflydb.io/docs/getting-started/benchmark — "Although Redis offers the `redis-benchmark` tool in its repository, it has not been as efficient as `memtier_benchmark` and it often becomes the bottleneck instead of Dragonfly."

### 4.3 redis-cli — all from https://redis.io/docs/latest/develop/tools/cli/
- `--eval <file>`: `redis-cli --eval myscript.lua key1 key2 , arg1 arg2 arg3` — "(Note: when using --eval the comma separates KEYS[] from ARGV[] items)" and "there is no need to specify the number of keys explicitly. Instead it uses the convention of separating keys and arguments with a comma." The comma must be its own argv token: `$ redis-cli --eval /tmp/script.lua location:hastings:temp , 23` → `OK`
- `--latency` — "Enter a special mode continuously sampling latency. Stats (min/max/avg) are reported in milliseconds with sub-millisecond precision." Sends PING 100×/s; non-TTY/`--raw`/`--csv` samples 1 s then exits.
- `--latency-history` — "Like --latency but tracking latency changes over time. Default time interval is 15 sec. Change it using -i."
- `--latency-dist` — "Shows latency as a spectrum, requires xterm 256 colors. Default time interval is 1 sec."
- `--intrinsic-latency <sec>` — "Run a test to measure intrinsic system latency." and "IMPORTANT: this command must be executed on the computer that runs the Redis server instance, not on a different host. It does not connect to a Redis instance and performs the test locally."
- `--pipe` — "Transfer raw Redis protocol from stdin to server." `--pipe-timeout <n>`: "Default timeout: 30. Use 0 to wait forever."
- `--latency-percentiles <p1,p2,...>` reports given percentiles in `--latency`/`--latency-history`.

### 4.4 Dragonfly's own benchmark skill — **PRESENT** **[DF]**
- https://github.com/dragonflydb/dragonfly/tree/main/.claude/skills/benchmark exists (`SKILL.md` ~13.9 KB, plus `evals/`, `references/`, `scripts/`). Sibling `.claude/skills/benchmark-report` also exists.
- SKILL.md frontmatter — `description: Benchmark Dragonfly (and compare against Valkey/Redis) on local or remote cloud instances, then produce a performance + memory report with charts and a raw-data appendix.` / `allowed-tools: Bash, Read, Write, Edit, AskUserQuestion`
- Six phases: gather spec → prepare instances → network tuning → start monitoring → run workloads → extract metrics/charts/report. `scripts/`: `bench_server.sh`, `bench_util.py`, `capture_result.sh`, `collect_metrics.py`, `make_charts.py`, `monitor_fill.sh`, `plot_fill.py`. `references/`: `running-benchmarks.md`, `report-template.md`. SKILL.md:75 — "**Run-control scripts are mandatory for benchmark phases.**"
- **Default load generator is `dfly_bench`, not memtier.** `references/running-benchmarks.md:142-164` — "dfly_bench (default load generator) ... Source: `src/server/dfly_bench.cc`" and "memtier_benchmark (alternative) ... Use it when the user wants cross-validation against a tool Dragonfly doesn't ship, or for the pipelined peak-throughput use-case."
- The memtier line it uses, `running-benchmarks.md:243-245` — `memtier_benchmark -s <server> -p 6380 --pipeline=30 --key-maximum=100000 -c 10 -t 2 --test-time=120 --distinct-client-seed --hide-histogram --json-out-file memtier_out.json`
- `dfly_bench` custom commands use the same placeholders plus `__score__`. `src/server/dfly_bench.cc:69-71` — `ABSL_FLAG(string, command, "", "custom command with __key__ placeholder for keys, __data__ for values, __score__ for doubles");`
- **Trap:** per `running-benchmarks.md`, when `--command` is used `--json_out_file` is ignored — parse the periodic `RPS(now/agg)` stdout lines instead.
- `dfly_bench.cc` has **no** EVAL/EVALSHA/Lua handling (grep `eval|lua|script` → 0 hits); substitution at `dfly_bench.cc:446-448`. EVALSHA is issuable but untested by Dragonfly's own harness. **UNVERIFIED** whether it handles a multi-arg EVALSHA numkeys correctly.
- Official page. https://www.dragonflydb.io/docs/getting-started/benchmark — "We benchmarked Dragonfly using the memtier_benchmark load testing tool." and "We also developed our own tool dfly_bench, which can be built from source in the Dragonfly repository." Example: `memtier_benchmark -s $SERVER_PRIVATE_IP --distinct-client-seed --hide-histogram --ratio 1:0 -t 60 -c 20 -n 200000`
- **Target-version caveat:** Dragonfly `v1.34.0` (published 2025-09-17) is released as `v1.34.0 (Use with caution - see known issues in description)`, listing known issues #5899 and #5891.

## 5. Client-side patterns

### 5.1 redis-py — https://github.com/redis/redis-py — **13,642 stars**
- `register_script(script)` returns a `Script`, defined in `redis/commands/core.py` (`ScriptCommands.register_script` → `return Script(self, script)`), **not** `redis/client.py`. Signature `Script.__call__(self, keys=None, args=None, client=None)`; builds `args = tuple(keys) + tuple(args)`.
- NOSCRIPT recovery, verbatim from `Script.__call__` (https://github.com/redis/redis-py/blob/master/redis/commands/core.py) — `try:` / `    return client.evalsha(self.sha, len(keys), *args)` / `except NoScriptError:` / `    self.sha = client.script_load(self.script)` / `    return client.evalsha(self.sha, len(keys), *args)`
- It reloads and retries **EVALSHA** — never falls back to plain EVAL — and overwrites `self.sha`, so a text/SHA discrepancy self-heals.
- In a pipeline it **buffers EVALSHA and pre-loads out of band**; it does not force EVAL. `Script.__call__` does `if isinstance(client, Pipeline): client.scripts.add(self)`; `Pipeline.execute` runs `if self.scripts: self.load_scripts()`. https://github.com/redis/redis-py/blob/master/redis/client.py (`Pipeline.load_scripts` ~L2199, called ~L2253) — `# we can't use the normal script_* methods because they would just` / `# get buffered in the pipeline.` / `exists = immediate("SCRIPT EXISTS", *shas)`
- This is the correct answer to the redis.io pipeline/NOSCRIPT warning in §1.8. `ClusterPipeline` is the exception: `# ClusterPipeline does not support script_load. Queue EVALSHA and leave NOSCRIPT recovery to the caller (same as AsyncScript).`

### 5.2 ioredis — https://github.com/redis/ioredis — **15,338 stars**
- `defineCommand`. https://github.com/redis/ioredis#lua-scripting (README:543-556) — `redis.defineCommand("myecho", {` / `  numberOfKeys: 2,` / `  lua: "return {KEYS[1],KEYS[2],ARGV[1],ARGV[2]}",` / `});` / `redis.myecho("k1", "k2", "a1", "a2", (err, result) => { /* ['k1','k2','a1','a2'] */ });` / `// and ioredis will try to use EVALSHA internally when possible for better performance.`
- Omitting `numberOfKeys` makes the key count the first call argument ("Dynamic Keys"); a `<name>Buffer` variant is auto-defined; a `scripts:` constructor option does the same declaratively.
- Motivation, README:541-542 — "it's tedious to use in real world scenarios since developers have to take care of script caching and to detect when to use EVAL and when to use EVALSHA."
- Two independent NOSCRIPT mechanisms in `lib/Script.ts` (capital S; `lib/script.ts` is a 404). https://github.com/redis/ioredis/blob/main/lib/Script.ts — `evalsha.promise = evalsha.promise.catch((err: Error) => {` / `  if (err.message.indexOf("NOSCRIPT") === -1) { throw err; }` — plus a per-socket `WeakSet` that rewrites the **first** send on each socket to EVAL then switches to EVALSHA: `if (!socketHasScriptLoaded.has(socket)) { …this.name = "eval"; this.args[0] = lua; }`
- Inspected on branch `main`, `package.json "version": "6.0.0"`. The v5 source layout is **UNVERIFIED**.
- Works in pipelines and cluster. README:566 — `// And of course it works with pipeline:` → `redis.pipeline().set("foo","bar").myecho("k1","k2","a1","a2").exec();`. README:1362 — "Almost all features that are supported by Redis are also supported by Redis.Cluster, e.g. custom commands, transaction and pipeline." `Script.ts:64` handles both: `const client = container.isPipeline ? container.redis : container;`

## 6. Dragonfly deltas — read before trusting §1–§2

Sources: https://www.dragonflydb.io/docs/managing-dragonfly/scripting (updated 2026-08-04) · https://www.dragonflydb.io/docs/command-reference/compatibility · https://www.dragonflydb.io/docs/managing-dragonfly/flags · `dragonflydb/dragonfly` tag `v1.34.0` (**31,532 stars**).

### 6.1 Redis Functions: NOT supported **[DF]**
- `FCALL`/`FCALL_RO` unimplemented; `FUNCTION` is a test stub accepting only `FLUSH`. https://github.com/dragonflydb/dragonfly/blob/v1.34.0/src/server/main_service.cc (`Service::Function` ~L2620; registered L3017 as `CI{"FUNCTION", CO::NOSCRIPT, 2, 0, 0, acl::kFunction}`) — `// Not a real implementation. Serves as a decorator to accept some function commands` / `// for testing.` — `grep -ci fcall main_service.cc` → 0.
- Compatibility table agrees: FCALL and FUNCTION * "Unsupported"; EVAL/EVALSHA/EVAL_RO/EVALSHA_RO "Fully supported".
- **One line: on Dragonfly v1.34.0 you must use EVAL/EVALSHA; Redis Functions do not exist.**

### 6.2 Also unsupported / partial **[DF]** — compatibility page
- `SCRIPT KILL` **Unsupported** · `SCRIPT DEBUG` **Unsupported** → the §1.10 escape hatch for a runaway script does not exist; the `lua-time-limit`/BUSY/SCRIPT KILL story is Redis-only. `SCRIPT FLUSH` "Partially supported (Missing: ASYNC, SYNC)".

### 6.3 Lua is 5.4, not 5.1 **[DF]** — biggest single divergence
- Scripting docs — "Dragonfly uses Lua version 5.4." and "Its interface for managing and writing scripts its compatible with the interface provided by Redis." (interface compatibility, *not* language-version compatibility)
- `LUAI_MAXCSTACK 8000` (§1.11) is a Lua-5.1/redis-deps constant and **does not transfer**; Dragonfly's `unpack` ceiling is **UNVERIFIED** — measure it.
- Lua 5.4 has a distinct integer subtype and `//`; 5.1 does not. Number→RESP edges deserve a direct test — Dragonfly ships `--lua_resp2_legacy_float` and `--lua_float_as_int_shas`, implying the float/int mapping has genuinely differed.
- Global `unpack` vs `table.unpack`, and the `bit` library, differ between 5.1 and 5.4. **UNVERIFIED** which Dragonfly exposes.

### 6.4 Flag syntax and supported set differ **[DF]**
- Dragonfly uses its own in-source directive, not the Redis shebang. Scripting docs — `--!df flags=allow-undeclared-keys`
- Three other routes: `--default_lua_flags=...` at startup, and `SCRIPT FLAGS <sha1> allow-undeclared-keys`, which "can be called even before the script is loaded."
- Only three values accepted. https://github.com/dragonflydb/dragonfly/blob/v1.34.0/src/server/script_mgr.cc (`ScriptParams::ApplyFlags` ~L382): `allow-undeclared-keys`, `disable-atomicity`, and `no-writes` — the last **accepted and ignored**: `// Used by Redis.` / `// TODO: lock read-only.` Anything else → `Invalid flag: `.
- So `allow-oom`, `allow-stale`, `no-cluster`, `allow-cross-slot-keys` **do not exist** on Dragonfly, and `no-writes` is a no-op — do not rely on it for replica routing or OOM behaviour.
- `disable-atomicity` is **UNVERIFIED** in prose docs; it appears only in `--default_lua_flags` help, `SCRIPT FLAGS` help, and `ApplyFlags`.

### 6.5 Undeclared keys and blast radius **[DF]** — the core performance fact
- Scripting docs — "Dragonfly forbids accessing undeclared keys from scripts and returns the following error: script tried accessing undeclared key"
- Why it's off by default, and its cost — "This option is disabled by default because unpredictability, atomicity and multithreading don't mix well. If enabled, Dragonfly has to stop all other operations when the script is running."
- Stated plainly. https://www.dragonflydb.io/docs/integrations/bullmq — "running Dragonfly with --default_lua_flags=allow-undeclared-keys locks the entire data store for each Lua script execution and slows things down considerably." Recommended instead: `./dragonfly --cluster_mode=emulated --lock_on_hashtags`
- Global locking needs **undeclared-keys AND atomic together**, not atomicity alone: `ScriptMgr::AreGlobalByDefault()` returns `default_params_.undeclared_keys && default_params_.atomic`.
- **Design rule:** on Dragonfly, declaring every key in KEYS is not documentation hygiene (as redis.io frames it) — it is the difference between shard-local locking and a stop-the-world lock on every EVAL.
- **UNVERIFIED:** no official reference page says "EVAL locks only the involved shards" in so many words. The per-shard claim is inferred from `AreGlobalByDefault()` plus the BullMQ contrast; the sharded-transaction background is on a dragonflydb.io blog post, not reference docs.

### 6.6 Dragonfly-specific flags **[DF]** — flags page, matching `ABSL_FLAG` in `script_mgr.cc:25-55`
- `--default_lua_flags`: "Configure default flags for running Lua scripts: Use `allow-undeclared-keys` to allow accessing undeclared keys, Use `disable-atomicity` to allow running scripts non-atomically."
- `--lock_on_hashtags`: "When true, locks are done in the {hashtag} level instead of key level. Only use this with `--cluster_mode=emulated|yes`."
- `--lua_auto_async` (default false): "If enabled, call/pcall with discarded values are automatically replaced with acall/apcall." → Dragonfly has **async `acall`/`apcall`** with no Redis equivalent; a script ignoring return values can be fire-and-forget. Worth benchmarking for write-heavy loops.
- `--lua_allow_undeclared_auto_correct`: "If enabled, when a script that is not allowed to run with undeclared keys is trying to access undeclared keys, automatically set the script flag to be able to run with undeclared key."
- Also present: `--lua_enable_redis_log`, `--lua_mem_gc_threshold`, `--lua_resp2_legacy_float`, `--lua_undeclared_keys_shas`, `--lua_float_as_int_shas`, `--luagc`, `--interpreter_per_thread`, `--multi_eval_squash_buffer`, `--multi_exec_squash`.
- Source-only, not on the flags page: `--lua_force_atomicity_shas` — "Comma-separated list of Lua script SHAs which are forced to run in atomic mode, even if the script specifies disable-atomicity." Both SHA lists ship hardcoded Sidekiq/Sentry defaults (`script_mgr.cc:41-55`) — evidence that real scripts have needed per-SHA overrides.

### 6.7 Sandbox differences **[DF]** — scripting docs
- `load()` is text-mode only; `rawset`/`setmetatable`/`getmetatable` are replaced with protected versions refusing to touch `_G`'s metatable; `dragonfly.randstr()` validates size. Note a `dragonfly.*` namespace exists alongside `redis.*`.
