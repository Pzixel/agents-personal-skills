# Lua patterns that hold on Dragonfly v1.34.0

Semantics here come from the Lua/RESP contract (Redis docs are usable for that, `[S21][S22][S24]`); every
*cost* claim is measured `[lab Qn][S1]` or cited to Dragonfly source. Lua is **5.4** on Dragonfly, not 5.1
`[lab Q9][S1]`.

## 1. Batching: fewer `redis.call`, same bytes

The unit of cost in regime (a) is the call, not the payload: 256 calls over 8 keys in one hashtag cost
26.95 µs each (2048B values, `--proactor_threads=4`, default flags, n=200) `[lab Q5][S1]`.

```lua
-- per item: N calls
for i = 1, #ids do
  local v = rcall('HGET', KEYS[1], ids[i])   -- one hop each in regime (a)
end

-- batched: ceil(N/chunk) calls, same bytes returned
local CHUNK = 512
local out, n = {}, 0
for i = 1, #ids, CHUNK do
  local hi = math.min(i + CHUNK - 1, #ids)
  local vals = rcall('HMGET', KEYS[1], unpack(ids, i, hi))
  for j = 1, #vals do n = n + 1; out[n] = vals[j] end
end
```

- **Chunk ceiling is the Lua stack, not a server setting**: `unpack` of 8163 fields into `redis.call`
  succeeds, 8164 fails with `@user_script: stack overflow`, and 16000 fails with
  `too many results to unpack` `[lab Q9][S1]` (same failure mode Redis documents at `LUAI_MAXCSTACK` `[S29]`).
  Chunk at 256-1000; 512 is what the measured `claim_mailbox_batch` variant uses.
- Writes batch the same way: 32x(5 `HSET` + 1 `ZADD`) = 192 calls becomes 5 multi-field `HSET` + 1
  multi-member `ZADD` = 6 calls, p50 5846 -> 539 µs, p99 9295 -> 1920 µs (standalone default-flags run,
  2048B values, n=200) `[lab Q2][S1]`.
- In regime (b) the same rewrite is worth much less: 9.9x on default flags vs 2.0x under
  `--lock_on_hashtags`, back to back `[lab Q10][S1]`; and inside a genuinely single-key script, chunked
  `HMGET` over 8192 fields is only 1.2x `[lab Q1][S1]`.
- `unpack(ARGV, 4)` for a variadic ARGV tail keeps the parameter block fixed-arity and avoids a Lua-side
  loop: `rcall('HSET', KEYS[1], unpack(ARGV, 4))` `[S26]`.
- Alias once at the top: `local rcall = redis.call` — 47 of BullMQ's 49 scripts do it, and it is mandatory
  if you splice shared fragments that call `rcall` `[S28]`.
- **Discarded replies**: `redis.acall` / `redis.apcall` buffer instead of flushing
  `[df-src interpreter.cc:677-683][S4]`; 256 discarded `HSET` over 8 keys/1 tag go 28.05 -> 2.93 µs/call
  `[lab Q5][S1]`. Never use them where you read the reply — the buffer is flushed on the next sync call
  anyway `[df-src CallFromScript][S2]`.
- A multi-key command is already split per shard by the server, so wrapping `MGET` in Lua saves nothing
  unless the keys co-locate `[df-doc][S18]`.

## 2. Reading only what the bound allows

Two separable things, with separate value:

1. **Prefetch the metadata for the whole window in one call** — pure batching, contract-neutral, and where
   the win is. The Q7 arm that measures it is the shipped hmget variant, which *also* narrows
   `record_missing` to the claimed candidates and defers the payload reads, so its 5.3x (p50 41183 ->
   7814 µs; 1024 candidates, batch 32, ~2048B records, 14 keys sharing one hashtag, standalone default
   flags, n=200, replies identical on the seeded input) covers (1)+(2) together `[lab Q7][S1]`. Q3 bounds
   (2) on its own at 1.1-1.8x, so the bulk of the win is the batching.
2. **Defer the expensive payload read to the items that survive the bound** — a contract change if the
   original checked something for every candidate. Isolated value: 1.1x standalone `[lab Q3][S1]`, 1.3x
   default / 1.8x `--lock_on_hashtags` back to back `[lab Q10][S1]`.

Do (1) unconditionally. Do (2) only with the data owner's decision, because narrowing a whole-window check
("is a record missing for **any** eligible candidate") to the claimed subset changes the reply on inputs
where the invariant does not hold — exactly the difference documented in the header of
`assets/examples/claim_mailbox_batch.hmget.lua`. `scripts/lua_call_audit.py --rule read-past-bound`
finds the shape.

## 3. cjson and large values

- `cjson`, `cmsgpack`, `struct`, `bit`, `redis.sha1hex`, global `unpack` and `table.unpack` are all present
  on v1.34.0 `[lab Q9][S1]`.
- Encoding cost is shard time, and the shard is single-threaded: a 1 MiB plan table `cjson.encode` + `HSET`
  is p50 1905 µs, 8 MiB is 15070 µs (50 iterations after 3 warm-up, `--maxmemory=2048Mi`) `[lab Q4][S1]`.
- At 32 MiB the first `encode`+`HSET` wrote 33766783 bytes and the **repeat failed** with
  `-ERR Out of memory`: overwriting a large hash field needs the old and the new blob resident at once
  `[lab Q4][S1]`. Size your `maxmemory` for 2x the largest blob you rewrite, or do not store it in one field.
- Do not quote Q4's concurrent-`GET` numbers as a blocking magnitude; that probe is run-to-run unstable and
  the controlled measurement is Q8 `[lab Q4, Q8][S1]`.
- Build the reply incrementally into a pre-declared table instead of encoding an accumulated structure at
  the end; `scripts/lua_call_audit.py --rule cjson-hot` flags the accumulate-then-encode shape.

## 4. Reply shape and error protocol

`[semantics]` (used throughout the skill) marks Lua/RESP behavior of the Redis-compatible API: documented
Redis-Lua semantics that Dragonfly implements, not a Dragonfly measurement or source citation.

- `redis.setresp` does not exist on Dragonfly, so RESP2 conversion is the only conversion a script sees and
  the usual RESP2 rules always apply (missing key as `false`, array truncated at the first `nil`, floats
  truncated) `[lab Q9][S1][semantics][S21]`. Keep arity constant, use a sentinel (`-1`, `''`) for absence,
  and return anything needing precision as a string.
- Prefer **integer status codes** over `redis.error_reply` on the hot path (`0` = duplicate, `1` = success;
  `1/2/0` = acquired/refreshed/taken), and let the caller map them to domain outcomes `[S26]`. Reserve
  error replies for protocol violations the caller cannot act on.
- Keep the KEYS/ARGV contract in a header comment and pin the key arity to the script identity — RQ binds
  each slot in a comment block `[S26]`, BullMQ encodes `numKeys` in the filename (`moveToFinished-9.lua`)
  `[S28]`. On Dragonfly this matters more than on Redis: the declared key set decides the execution path
  `[df-src CanRunSingleShardMulti][S2]`.
- Script composition by string concatenation changes the SHA and silently shifts ARGV meaning; prefer an
  include mechanism `[S26][S28]`.

## 5. Calling the script

- Load once per process. A fresh-text `SCRIPT LOAD` of an 8894B script against re-loading an already-cached
  sha costs p50 499 vs 162 µs idle, and 478 vs 149 µs under 4 loaders (default flags, n=200)
  `[lab Q11d][S1]`, and it borrows an interpreter from
  the per-thread pool of 10 `[df-doc PR #8300][S11][df-src interpreter.cc][S4]`.
- Batch size is sometimes a durability decision, not a throughput one — Sidekiq deliberately pops one job
  per `EVALSHA` to bound what a crash can lose `[S25]`. Do not "optimize" a bound you did not write.
- `EVAL_RO` / `EVALSHA_RO` exist on v1.34.0 and reject writes at call time
  `[lab Q9][S1][df-src main_service.cc:1358][S2]`.
