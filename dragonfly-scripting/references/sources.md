# Sources

Every rule in `SKILL.md` and the other references carries an `[S<n>]` tag pointing at a row here, plus an
evidence tag saying *what kind* of evidence it is: `[lab Qn]` (measured, `S1`), `[df-src]` (Dragonfly
v1.34.0 source), `[df-doc]` (Dragonfly docs/blog/issue), `[semantics]` (Lua/RESP behavior of the
Redis-compatible API, defined in `lua-patterns.md §4`). A `[df-src]`/`[df-doc]` tag may name the file,
symbol or URL when that points at the exact evidence, or stand bare when the `[S<n>]` row beside it already
carries the location.

Tiers: **A** official docs · **B** primary source/repo/issue · **C** vendor blog · **D** academic.
Redis-tier rows (S21-S23, S29) explain **Lua/protocol semantics only** — no performance rule in this skill
rests on them.

| S | title | URL | tier | why it is cited |
|---|-------|-----|------|-----------------|
| S1 | Dragonfly scripting lab, Q1-Q11 on df-v1.34.0 | (measured; conditions and cells in `references/measurements.md`) | measured | every number in this skill |
| S2 | Dragonfly v1.34.0 `src/` tree (`main_service.cc`, `transaction.cc`, `multi_command_squasher.cc`, `server_family.cc`, `facade/error.h`) | https://github.com/dragonflydb/dragonfly/tree/v1.34.0/src | B | the single-shard fast path, what disables it, squashing, the `eval_*` counters |
| S3 | Dragonfly `src/server/script_mgr.cc` | https://github.com/dragonflydb/dragonfly/blob/v1.34.0/src/server/script_mgr.cc | B | `--!df flags=` parsing, the three flags, `--lua_auto_async` gate |
| S4 | Dragonfly `src/core/interpreter.cc` | https://github.com/dragonflydb/dragonfly/blob/v1.34.0/src/core/interpreter.cc | B | `redis.acall`/`apcall` registration, interpreter pool blocking |
| S5 | Dragonfly `docs/transaction.md` (tag v1.34.0) | https://github.com/dragonflydb/dragonfly/blob/v1.34.0/docs/transaction.md | B | hops, scheduling, LOCK_AHEAD/GLOBAL/NON_ATOMIC, squashing |
| S6 | Dragonfly `docs/df-share-nothing.md` | https://github.com/dragonflydb/dragonfly/blob/main/docs/df-share-nothing.md | B | one thread owns one shard; cross-thread = message passing |
| S7 | Dragonfly docs — Scripting with Lua | https://www.dragonflydb.io/docs/managing-dragonfly/scripting | A | declared keys, `--!df` directive, undeclared-key global lock |
| S8 | Dragonfly docs — Server configuration flags | https://www.dragonflydb.io/docs/managing-dragonfly/flags | A | flag names and documented defaults |
| S9 | Dragonfly docs — Command compatibility | https://www.dragonflydb.io/docs/command-reference/compatibility | A | `FUNCTION`/`FCALL`/`SCRIPT KILL` unsupported |
| S10 | Dragonfly issue #8269 — no script timeout / no `SCRIPT KILL` | https://github.com/dragonflydb/dragonfly/issues/8269 | B | a runaway script pins its thread until `kill -9` |
| S11 | Dragonfly PR #8300 — script load interpreter wait | https://github.com/dragonflydb/dragonfly/pull/8300 | B | `SCRIPT LOAD` borrows an interpreter; load once, not per worker |
| S12 | Dragonfly issue #6006 — redesign pipelining support | https://github.com/dragonflydb/dragonfly/issues/6006 | B | squashing a pipeline is a CPU hot path (~20% in `SquashPipeline`) |
| S13 | Dragonfly blog — rate limiting API (`CL.THROTTLE`) | https://www.dragonflydb.io/blog/introducing-a-rate-limiting-api-by-dragonfly | C | native single-key FAST rate limiter |
| S14 | Dragonfly blog — from dict to DashTable | https://www.dragonflydb.io/blog/from-dict-to-dashtable-how-dragonfly-cuts-memory-overhead-by-40 | C | ~18.6 B/key overhead, no rehash spike |
| S15 | Dragonfly blog — the hidden bottlenecks of scaling out | https://www.dragonflydb.io/blog/the-hidden-bottlenecks-of-scaling-out | C | key skew concentrates heat on one shard |
| S16 | Dragonfly blog — a preview of Dragonfly Cluster | https://www.dragonflydb.io/blog/a-preview-of-dragonfly-cluster | C | cluster mode: a script's keys must share one CRC16 slot |
| S17 | Dragonfly `docs/differences.md` | https://github.com/dragonflydb/dragonfly/blob/main/docs/differences.md | B | expirations above 2^28 ms are rounded to the nearest second |
| S18 | Dragonfly guides — Redis and Dragonfly architecture comparison | https://www.dragonflydb.io/guides/redis-and-dragonfly-architecture-comparison | C | a multi-key command is already split per shard |
| S19 | Dragonfly blog — BullMQ part 2, optimization | https://www.dragonflydb.io/blog/running-bullmq-with-dragonfly-part-2-optimization | C | 2.26x from an application-side hashtag change |
| S20 | Dragonfly docs — INFO command reference | https://www.dragonflydb.io/docs/command-reference/server-management/info | A | the `eval_*`/`lua_*` counters are undocumented there |
| S21 | Redis docs — Lua API reference | https://redis.io/docs/latest/develop/programmability/lua-api/ | A | reply conversion, `call` vs `pcall`, globals protection (semantics only) |
| S22 | Redis docs — Scripting with Lua (eval-intro) | https://redis.io/docs/latest/develop/programmability/eval-intro/ | A | `#!lua` shebang flags, the Redis contract this skill contradicts |
| S23 | Redis docs — Pipelining | https://redis.io/docs/latest/develop/using-commands/pipelining/ | A | the "prefer pipelining over Lua" advice being audited |
| S24 | Lua 5.4 reference manual | https://www.lua.org/manual/5.4/manual.html | A | integer subtype, `table.unpack`, 5.4-vs-5.1 differences |
| S25 | Sidekiq `lib/sidekiq/scheduled.rb` | https://github.com/sidekiq/sidekiq/blob/main/lib/sidekiq/scheduled.rb | B | minimal NOSCRIPT reload-once protocol; batch size as durability choice |
| S26 | RQ `rq/scripts.py` | https://github.com/rq/rq/blob/master/rq/scripts.py | B | KEYS/ARGV header contract, integer status codes, `unpack(ARGV, i)` |
| S27 | ioredis `lib/Script.ts` | https://github.com/redis/ioredis/blob/main/lib/Script.ts | B | NOSCRIPT must be detected by typed error *and* raw prefix in a pipeline |
| S28 | BullMQ `src/commands` | https://github.com/taskforcesh/bullmq/tree/master/src/commands | B | `numKeys` in the filename; `local rcall = redis.call` convention |
| S29 | Redis `src/script_lua.c` / `luaconf.h` (`LUAI_MAXCSTACK`) | https://github.com/redis/redis/blob/unstable/src/script_lua.c | B | where the `unpack` ceiling comes from (Dragonfly's own limit is measured, Q9) |
| S30 | Dragonfly PR #6277 — locking control from Lua scripts | https://github.com/dragonflydb/dragonfly/pull/6277 | B | lock tags and per-script locking control |
