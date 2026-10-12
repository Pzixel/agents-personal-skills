# What a Redis-trained model gets wrong on Dragonfly v1.34.0

Every row was probed on v1.34.0 (`redis_version 7.4.0`) or read out of the v1.34.0 source. "Redis habit"
is the belief; "Dragonfly" is what actually happens.

## Loading and flags

| Redis habit | Dragonfly v1.34.0 |
|---|---|
| `#!lua flags=...` on line 1 configures the script, and the flag set is Redis's (`no-writes`, `allow-oom`, ...), and adding flags makes a script faster | Every part of that is wrong here: the shebang is a Lua syntax error, the directive is `--!df flags=...`, only `disable-atomicity`/`allow-undeclared-keys` do anything, and both **remove** the fast path — the directive table with measured costs is in `server-flags.md` |
| Keys not in `KEYS` are merely bad style | Undeclared access is rejected (`script tried accessing undeclared key`), and enabling it promotes the script to a GLOBAL transaction that locks every shard `[df-src facade/error.h, DetermineMultiMode][S2][S7]` |
| `SCRIPT FLAGS <sha>` reads a script's flags | It is a **setter only** — with no flag argument it errors. It may be called *before* the script is loaded, which is how you patch a framework script you cannot edit `[lab Q9][S1]` |

## Missing and extra API

| Redis habit | Dragonfly v1.34.0 |
|---|---|
| `FUNCTION LOAD` / `FCALL` are the modern replacement for EVAL `[S22]` | Do not exist. Use `EVAL`/`EVALSHA` `[lab Q9][S1][df-doc][S9]` |
| `SCRIPT KILL` rescues a runaway script; `busy-reply-threshold` bounds it | No timeout, no `SCRIPT KILL`, no BUSY reply, no instruction hook. An infinite script pins its thread permanently and recovery is `kill -9`. Bounding the work is your job `[df-doc issue #8269][S10][lab Q9][S1]` |
| `redis.setresp(3)` switches reply conversion | `redis.setresp` is `nil`; calling it errors `[lab Q9][S1]` |
| `redis.replicate_commands()` selects effects replication | A no-op stub — Dragonfly replicates script **effects** unconditionally `[df-src interpreter.cc][S4]` |
| `redis.log` writes to the server log | Silent unless `--lua_enable_redis_log=true`, and the function only exists from v1.33.0 `[df-src interpreter.cc][S4]` |
| The `redis` table is the whole API | Dragonfly adds `redis.acall` / `redis.apcall` (buffered, squashed — use for discarded replies: 28.05 -> 2.93 µs/call on 256 discarded `HSET`, 8 keys/1 tag) and a `dragonfly` global with `ihash`/`randstr` `[df-src interpreter.cc:677-683][S4][lab Q5][S1]` |
| `SCRIPT STATS`, `MEMORY DOCTOR`, `LATENCY HISTORY` help you profile | All error. What exists: `SCRIPT LIST`, `SCRIPT EXISTS`, `SCRIPT LATENCY` (microsecond histograms, cumulative for the server lifetime, not reset by `SCRIPT FLUSH`), `SCRIPT GC`, `DEBUG OBJHIST`, `MEMORY USAGE`, and the `eval_*`/`lua_*` `INFO` counters `[lab Q9][S1]` |

## Lua runtime

| Redis habit | Dragonfly v1.34.0 |
|---|---|
| Lua 5.1 semantics: no integer subtype, `unpack` is global, `bit` library | **Lua 5.4.4** `[df-doc docs/differences.md][S17]`; `_VERSION` probes as `Lua 5.4` `[lab Q9][S1]`. Integers are a real type `[S24]`. Both `unpack` and `table.unpack` exist and `cjson`, `cmsgpack`, `struct`, `bit`, `redis.sha1hex` are present `[lab Q9][S1]`; no `os`, `io`, `package`, and `debug` is `nil` `[df-src interpreter.cc:352-361][S4]` |
| `unpack` dies around `LUAI_MAXCSTACK` = 8000 `[S29]` | Measured ceiling: 8163 fields succeed, 8164 fails with `stack overflow`, 16000 with `too many results to unpack` `[lab Q9][S1]`. Chunk at 256-1000 either way |
| Float replies round-trip | Floats truncate to integers and a Lua array truncates at the first `nil` (string-keyed fields are dropped) — same as Redis, and still the most common reply bug. Return precision-critical numbers as strings `[S21]` |

## Placement, transactions and time

| Redis habit | Dragonfly v1.34.0 |
|---|---|
| A shared `{hashtag}` co-locates keys | **Not by default.** 8 keys in one tag cost the same per `redis.call` as 8 keys in eight tags (26.95 vs 27.00 µs, 256 `HGET`, 2048B, `--proactor_threads=4`, n=200) and both report `eval_io_coordination_total +1`. Only `--lock_on_hashtags` makes the tag the placement/lock unit (then 1.54 µs/call) `[lab Q5][S1][df-src lock_tags][S2][S30]` |
| Hashtags matter only in cluster mode | In **cluster mode** they are a correctness requirement (a script's keys must share one CRC16 slot, else `-MOVED`) `[df-doc][S16]`; outside it they matter only with `--lock_on_hashtags` `[lab Q5][S1]` |
| Redis is single-threaded, so a script blocks everything | Dragonfly is shard-per-thread `[df-doc][S6]`. Head-of-line blocking is a proactor-count question on default flags: 8 loaders x ~2 ms script, a 9th client's `GET` p99 is 33219 µs at `--proactor_threads=1` and 169 µs at 4, regardless of the probe's hashtag `[lab Q8][S1]` |
| `MULTI`/`EXEC` and a script give the same isolation | A script is one multi-transaction in `LOCK_AHEAD` mode over its declared keys `[S5]`; MULTI/EXEC squashing is disabled when the block runs a script `[df-src Service::Exec][S2]`, and their measured costs diverge in both directions depending on the regime `[lab Q11a][S1]` |
| Millisecond TTLs are exact | Expirations greater than 2^28 ms (~74 h) are quietly rounded to the nearest second, and expiry caps at 8 years. A lock or limiter script computing long ms TTLs diverges from Redis `[df-doc docs/differences.md][S17]` |
| Wrapping `MGET` in Lua makes it one operation | Multi-key commands are already split per shard and run on the relevant threads; Lua adds nothing unless the keys co-locate `[df-doc][S18]` |

## Advice that inverts

Pipelining-vs-scripting, one-big-hash-vs-many-keys and Lua-vs-`CL.THROTTLE` all flip direction here; the
audit table with verdicts and numbers is `redis-advice-audit.md`.
