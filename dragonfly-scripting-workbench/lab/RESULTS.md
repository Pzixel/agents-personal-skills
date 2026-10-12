# Dragonfly v1.34.0 scripting lab — results

Lab: `lab/compose.yaml` (primary :6379, replica :6380, `single` profile :6381),
image digest `sha256:366e34f4…95db`, args
`--cache_mode=false --maxmemory=2048Mi --dbfilename=dump --proactor_threads=4`.
Timings are client-side wall clock in microseconds, reported as-is
(no smoothing, no outlier removal). Regenerate with `lab/run_all.sh`.

## Summary

| Q | experiment | key numbers | conclusion |
|---|---|---|---|
| Q1 | N x HGET vs HMGET/256, 2048B values | N=8192: 5496us vs 4698us p50 (1.2x); 0.67us per redis.call against ONE key | against one key (shard-local) a redis.call costs ~0.67us at N=8192 and 5.1us at N=32; across 8 keys Q5 measures 26.95us/call -- batch with HMGET |
| Q2 | 32x(5 HSET+1 ZADD) vs 5 multi-HSET + 1 multi-ZADD | p50 5846us -> 539us (10.8x); p99 9295us -> 1920us | 192 -> 6 redis.call is a 10.8x win; batch writes (standalone run, no co-tenant; Q10 re-measures the same workload back to back against `--lock_on_hashtags` and reports a different ratio -- see the reconciliation note in Q10) |
| Q3 | 1024 candidates, payload for all vs first 32 | p50 34136us -> 31971us (1.1x); p99 50827us -> 44045us | fetch payloads only for the chosen 32; metadata scan still costs 31971us (standalone run, no co-tenant; Q10 re-measures the same workload back to back against `--lock_on_hashtags` and reports a different ratio -- see the reconciliation note in Q10) |
| Q4 | cjson.encode 1/8/32 MiB command table, HSET then HDEL | script p50 1905us at 1 MiB; worst concurrent GET p99 375us vs 155us idle | concurrent GET degrades but stays sub-millisecond here; this run does NOT show millisecond shard blocking -- see Q8 (part B) for the controlled head-of-line measurement; see the table for the largest size that completes |
| Q5 | 256 HGET, 1 key vs 8 keys/1 tag vs 8 keys/8 tags (+disable-atomicity, lock_on_hashtags, lua_auto_async) | us/call 1.01 (1 key) / 26.95 (1 tag) / 27.00 (8 tags); hop 25.26 vs 23.17 | see verdict in the appendix |
| Q6 | Q2 write scripts, primary with replica attached vs detached | mean p50 attached vs standalone: per-call 4679 vs 4484us (+4.3%), batched 569 vs 402us (+41.6%) | difference is beyond (per-call) / beyond (batched) the spread between replicates of the same condition |
| Q7 | claim_mailbox_batch.lua, 1024 due, batch 32, ~2048B records: per-candidate HGET vs chunked HMGET | p50 41183us -> 7814us (5.3x); p99 52222us -> 17150us | replies identical; prefetch + deferred payload is a 5.3x win (standalone run, no co-tenant; Q10 re-measures the same workload back to back against `--lock_on_hashtags` and reports a different ratio -- see the reconciliation note in Q10) |
| Q8 | 8 clients x ~2000us script, 9th client GET p99, threads 1 vs 4, probe in same vs different hashtag | p99 t1/same 33219us, t1/diff 33084us, t4/same 169us, t4/diff 191us | see the four cells in the appendix |
| Q9 | inventory of SCRIPT\*/DEBUG/INFO/flags | 43 probes, 20 server flags | SCRIPT LATENCY+FLAGS+LIST+GC exist; SCRIPT STATS does not; FLAGS is a setter only; histograms never reset |
| Q10 | Q2/Q3/Q7 workloads re-run on default vs `--lock_on_hashtags`, back to back | batching 9.9x -> 2.0x; payload skip 1.3x -> 1.8x; claim_mailbox_batch hmget 7.6x -> 2.6x | see the 4-cell Q7 table and the counter split in the appendix; the flag's cost under concurrent load is in Q11(d) |
| Q11 | pipeline vs Lua vs MULTI; 100k keys vs 1 hash; Lua limiter vs CL.THROTTLE; SCRIPT LOAD under load | 64 GET: script/pipeline p50 3.9x default, 0.4x lock_on_hashtags; Lua limiter/CL.THROTTLE 0.9x default, 0.8x lock_on_hashtags; 8894B SCRIPT LOAD p50 499us idle -> 478us under 4 loaders (cached sha 149us); 100k x 64B costs 83.7 B/value as strings vs 109.6 in one hash | see the four sub-experiments in the appendix |

## Appendix — per-question detail
<!--BEGIN q1-->
<!--SUMMARY| Q1 | N x HGET vs HMGET/256, 2048B values | N=8192: 5496us vs 4698us p50 (1.2x); 0.67us per redis.call against ONE key | against one key (shard-local) a redis.call costs ~0.67us at N=8192 and 5.1us at N=32; across 8 keys Q5 measures 26.95us/call -- batch with HMGET |-->
### Q1 — redis.call overhead inside Lua

200 measured iterations after 20 warm-up, per cell. Both variants return the same byte total (asserted). The workload is a SINGLE hash, i.e. the shard-local execution path; Q5 measures the same 256-call shape across 8 keys at 26.95us/`redis.call`, ~40x more, so the per-call figures below apply to single-key scripts only.

| N    | per-call p50 us | HMGET p50 us | speedup x | per-call us/redis.call | HMGET us/redis.call |
|------|-----------------|--------------|-----------|------------------------|---------------------|
| 32   | 164.2           | 118.9        | 1.4       | 5.1                    | 118.9               |
| 256  | 263.8           | 216.7        | 1.2       | 1.0                    | 216.7               |
| 1024 | 916.6           | 766.3        | 1.2       | 0.9                    | 191.6               |
| 8192 | 5496.4          | 4698.0       | 1.2       | 0.7                    | 146.8               |

Per-variant client latency:

| variant              | n   | p50_us | p95_us | p99_us | min_us | max_us  | mean_us | total_us  | redis_calls | us_per_call(p50) |
|----------------------|-----|--------|--------|--------|--------|---------|---------|-----------|-------------|------------------|
| N=32 per-call HGET   | 200 | 164.2  | 253.0  | 483.3  | 134.1  | 3596.8  | 194.0   | 38801.7   | 32          | 5.1              |
| N=32 HMGET/256       | 200 | 118.9  | 155.5  | 215.6  | 81.0   | 536.6   | 123.1   | 24614.7   | 1           | 118.9            |
| N=256 per-call HGET  | 200 | 263.8  | 319.2  | 506.9  | 229.0  | 723.4   | 274.7   | 54935.3   | 256         | 1.0              |
| N=256 HMGET/256      | 200 | 216.7  | 258.0  | 285.0  | 171.0  | 306.7   | 218.9   | 43786.3   | 1           | 216.7            |
| N=1024 per-call HGET | 200 | 916.6  | 1040.8 | 1132.9 | 770.4  | 1244.8  | 921.6   | 184313.4  | 1024        | 0.9              |
| N=1024 HMGET/256     | 200 | 766.3  | 961.0  | 1127.2 | 598.1  | 1376.8  | 781.7   | 156330.2  | 4           | 191.6            |
| N=8192 per-call HGET | 200 | 5496.4 | 6202.1 | 6862.4 | 4947.2 | 11949.5 | 5600.8  | 1120164.1 | 8192        | 0.7              |
| N=8192 HMGET/256     | 200 | 4698.0 | 5575.6 | 6434.0 | 3964.4 | 6809.7  | 4746.7  | 949345.4  | 32          | 146.8            |

Server-side `SCRIPT LATENCY` histograms for both shas are in the raw log (they include the 20 warm-up iterations).

Raw log: [`results/q1.txt`](results/q1.txt)
<!--END q1-->

<!--BEGIN q2-->
<!--SUMMARY| Q2 | 32x(5 HSET+1 ZADD) vs 5 multi-HSET + 1 multi-ZADD | p50 5846us -> 539us (10.8x); p99 9295us -> 1920us | 192 -> 6 redis.call is a 10.8x win; batch writes (standalone run, no co-tenant; Q10 re-measures the same workload back to back against `--lock_on_hashtags` and reports a different ratio -- see the reconciliation note in Q10) |-->
### Q2 — write batching (multi-field HSET / multi-member ZADD)

200 measured iterations after 20 warm-up. All 6 keys declared in `KEYS`, one hashtag `{q2}`.

| variant                                  | n   | p50_us | p95_us | p99_us | min_us | max_us  | mean_us | total_us  | redis_calls | us_per_call(p50) |
|------------------------------------------|-----|--------|--------|--------|--------|---------|---------|-----------|-------------|------------------|
| 32x(5 HSET + 1 ZADD)                     | 200 | 5845.8 | 7770.3 | 9294.6 | 3226.0 | 11642.6 | 5912.5  | 1182509.0 | 192         | 30.4             |
| 5 multi-field HSET + 1 multi-member ZADD | 200 | 539.4  | 1059.0 | 1920.2 | 339.1  | 2606.0  | 590.5   | 118095.1  | 6           | 89.9             |

Batching collapses 192 `redis.call` crossings into 6 and is **10.8x** faster at p50 (5846us -> 539us); p99 9295us -> 1920us. Server-side histograms in the raw log.

Raw log: [`results/q2.txt`](results/q2.txt)
<!--END q2-->

<!--BEGIN q3-->
<!--SUMMARY| Q3 | 1024 candidates, payload for all vs first 32 | p50 34136us -> 31971us (1.1x); p99 50827us -> 44045us | fetch payloads only for the chosen 32; metadata scan still costs 31971us (standalone run, no co-tenant; Q10 re-measures the same workload back to back against `--lock_on_hashtags` and reports a different ratio -- see the reconciliation note in Q10) |-->
### Q3 — read-after-full vs stop after the batch

200 measured iterations after 20 warm-up. Metadata (version + lease) is read for all 1024 candidates in both variants; only the payload reads differ (1024 vs 32 x 2048B).

| variant              | n   | p50_us  | p95_us  | p99_us  | min_us  | max_us  | mean_us | total_us  | redis_calls | payload_KiB |
|----------------------|-----|---------|---------|---------|---------|---------|---------|-----------|-------------|-------------|
| payload for all 1024 | 200 | 34135.8 | 41485.6 | 50827.4 | 17051.4 | 53150.2 | 34240.0 | 6847992.1 | 3072        | 2048        |
| payload for first 32 | 200 | 31971.2 | 35226.7 | 44045.4 | 15493.2 | 64056.2 | 31030.8 | 6206168.9 | 2080        | 64          |

Dropping 992 payload `HGET`s (1984 KiB) takes p50 from 34136us to 31971us (**1.1x**), p99 50827us -> 44045us. The remaining 2048 metadata calls dominate the residual cost.

Raw log: [`results/q3.txt`](results/q3.txt)
<!--END q3-->

<!--BEGIN q4-->
<!--SUMMARY| Q4 | cjson.encode 1/8/32 MiB command table, HSET then HDEL | script p50 1905us at 1 MiB; worst concurrent GET p99 375us vs 155us idle | concurrent GET degrades but stays sub-millisecond here; this run does NOT show millisecond shard blocking -- see Q8 (part B) for the controlled head-of-line measurement; see the table for the largest size that completes |-->
### Q4 — big cjson plan on the success path

50 measured iterations per size after 3 warm-up (fewer than the usual 200 because a 32 MiB `cjson.encode` is ~100 ms-scale work). `--maxmemory=2048Mi`.

Time in script:

| variant                      | n  | p50_us  | p95_us  | p99_us  | min_us  | max_us  | mean_us | total_us | blob_MiB | error                                                        |
|------------------------------|----|---------|---------|---------|---------|---------|---------|----------|----------|--------------------------------------------------------------|
| 1 MiB plan encode+HSET       | 50 | 1904.6  | 2020.5  | 2135.9  | 1700.9  | 2135.9  | 1902.2  | 95110.1  | 1.00     |                                                              |
| 1 MiB plan encode+HSET+HDEL  | 20 | 2054.1  | 2299.2  | 2299.2  | 1845.1  | 2299.2  | 2059.3  | 41186.7  |          |                                                              |
| 8 MiB plan encode+HSET       | 50 | 15069.9 | 18496.6 | 18598.2 | 13291.5 | 18598.2 | 15313.3 | 765666.4 | 8.04     |                                                              |
| 8 MiB plan encode+HSET+HDEL  | 20 | 15625.6 | 16971.1 | 16971.1 | 14774.7 | 16971.1 | 15714.6 | 314292.8 |          |                                                              |
| 32 MiB plan encode+HSET      | 0  | -       | -       | -       | -       | -       | -       | -        | 32.20    | ResponseError: Error running script (call to a3ddf54d8ac5daf |
| 32 MiB plan encode+HSET+HDEL | 0  | -       | -       | -       | -       | -       | -       | -        |          | ResponseError: Error running script (call to a3ddf54d8ac5daf |

Plain `GET` from a second client, concurrent with the run (shard blocking):

| during           | GET p50_us | GET p95_us | GET p99_us | GET max_us | n             |
|------------------|------------|------------|------------|------------|---------------|
| (baseline, idle) | 61.9       | 91.8       | 155.1      | 29586.2    | 29467 samples |
| 1 MiB plan       | 84.3       | 147.8      | 220.9      | 325.4      | 1055 samples  |
| 8 MiB plan       | 60.0       | 104.0      | 183.6      | 1035.6     | 12010 samples |
| 32 MiB plan      | 86.5       | 186.8      | 375.1      | 19927.4    | 28971 samples |

**32 MiB plan: the FIRST encode+HSET succeeded (33766783 bytes) but repeating it failed with `ResponseError: Error running script (call to a3ddf54d8ac5daff971f428e1ded569f149ca6a9): @user_script:11: -ERR Out of memory`** -- with `--maxmemory=2048Mi`, overwriting a 32 MiB hash field needs the old and the new blob resident at once. Recorded as the result.

The concurrent-`GET` figures are **run-to-run unstable**: the probe key `{q4probe}g` and the plan key `{q4}plan` are separate keys, and whether the probe lands on the proactor running the script decides everything. An earlier run of this benchmark was observed to serialise the probe behind the script, but that run was not retained in a raw log, so no figure from it is quoted here. The sample counts also differ by an order of magnitude between rows, over different wall-clock windows. The blocking magnitude therefore cannot be read off this table; see Q8 (part B) for the controlled same-shard vs other-shard measurement.

Raw log: [`results/q4.txt`](results/q4.txt)
<!--END q4-->

<!--BEGIN q5-->
<!--SUMMARY| Q5 | 256 HGET, 1 key vs 8 keys/1 tag vs 8 keys/8 tags (+disable-atomicity, lock_on_hashtags, lua_auto_async) | us/call 1.01 (1 key) / 26.95 (1 tag) / 27.00 (8 tags); hop 25.26 vs 23.17 | see verdict in the appendix |-->
### Q5 — key placement (1 key vs 8 keys/1 tag vs 8 keys/8 tags)

200 measured iterations after 20 warm-up; every variant issues exactly **256 `redis.call`** with 2048B values, so `us_per_call` is directly comparable. `us_per_call(p50)` = client p50 / 256.

**default flags (`--proactor_threads=4`, primary :6379)**

| variant                                               | n   | p50_us | p95_us | p99_us | min_us | max_us  | mean_us | total_us  | shape        | us_per_call(p50) | shardlocal/io per call |
|-------------------------------------------------------|-----|--------|--------|--------|--------|---------|---------|-----------|--------------|------------------|------------------------|
| 256 HGET / 1 key (shard-local) / atomic               | 200 | 259.5  | 286.2  | 300.7  | 242.0  | 306.8   | 261.8   | 52362.1   | 1k/1tag      | 1.0              | 1.00/0.00              |
| 256 HGET / 1 key (shard-local) / disable-atomicity    | 200 | 315.9  | 343.2  | 359.8  | 288.3  | 372.1   | 318.2   | 63639.0   | 1k/1tag      | 1.2              | 0.00/1.00              |
| 256 HGET / 8 keys, 1 hashtag / atomic                 | 200 | 6899.0 | 7734.9 | 9280.5 | 3285.7 | 9950.6  | 6601.8  | 1320367.1 | 8k/1tag      | 26.9             | 0.00/1.00              |
| 256 HGET / 8 keys, 1 hashtag / disable-atomicity      | 200 | 6751.4 | 7214.8 | 8200.2 | 3352.7 | 13259.5 | 5914.6  | 1182923.5 | 8k/1tag      | 26.4             | 0.00/1.00              |
| 256 HGET / 8 keys, 8 hashtags / atomic                | 200 | 6912.7 | 7205.5 | 9207.0 | 4074.0 | 11893.0 | 6971.3  | 1394261.1 | 8k/8tag      | 27.0             | 0.00/1.00              |
| 256 HGET / 8 keys, 8 hashtags / disable-atomicity     | 200 | 6881.6 | 7155.1 | 7433.0 | 3479.1 | 8200.6  | 6784.6  | 1356927.4 | 8k/8tag      | 26.9             | 0.00/1.00              |
| 256 HGET / 8 keys, 1 hashtag / allow-undeclared-keys  | 200 | 6968.2 | 8243.0 | 9917.5 | 3227.5 | 10775.5 | 6690.2  | 1338033.6 | 0k decl/1tag | 27.2             | 0.00/1.00              |
| 256 HGET / 8 keys, 8 hashtags / allow-undeclared-keys | 200 | 6955.1 | 7503.8 | 8168.8 | 3452.2 | 9484.2  | 6646.0  | 1329191.2 | 0k decl/8tag | 27.2             | 0.00/1.00              |
| 256 HSET (discarded) / 8 keys, 1 hashtag              | 200 | 7180.5 | 7653.2 | 8093.1 | 3517.8 | 10627.3 | 7029.2  | 1405835.0 | 8k/1tag      | 28.0             | 0.00/1.00              |
| 256 HSET (discarded) / 8 keys, 8 hashtags             | 200 | 7285.2 | 7746.7 | 7891.3 | 3609.3 | 7977.4  | 6798.5  | 1359700.1 | 8k/8tag      | 28.5             | 0.00/1.00              |

**hop microbenchmark: 256 `GET` on 256 distinct keys (default flags)**

| variant              | n   | p50_us | p95_us | p99_us  | min_us | max_us  | mean_us | total_us  | shape     | us_per_call(p50) | shardlocal/io per call |
|----------------------|-----|--------|--------|---------|--------|---------|---------|-----------|-----------|------------------|------------------------|
| 256 GET / 1 hashtag  | 200 | 6466.5 | 8661.3 | 11159.2 | 3151.3 | 13845.2 | 6704.1  | 1340824.6 | 256k/1tag | 25.3             | 0.00/1.00              |
| 256 GET / 4 hashtags | 200 | 5930.2 | 6573.0 | 6990.5  | 2833.7 | 7601.8  | 5159.3  | 1031865.8 | 256k/4tag | 23.2             | 0.00/1.00              |

**`--lock_on_hashtags` (throwaway node :6382)**

| variant                                               | n   | p50_us | p95_us | p99_us  | min_us | max_us  | mean_us | total_us  | shape        | us_per_call(p50) | shardlocal/io per call |
|-------------------------------------------------------|-----|--------|--------|---------|--------|---------|---------|-----------|--------------|------------------|------------------------|
| 256 HGET / 1 key (shard-local) / atomic               | 200 | 373.1  | 631.3  | 1290.7  | 309.2  | 3749.9  | 428.6   | 85722.0   | 1k/1tag      | 1.5              | 1.00/0.00              |
| 256 HGET / 1 key (shard-local) / disable-atomicity    | 200 | 357.8  | 444.5  | 499.2   | 287.5  | 1419.1  | 366.8   | 73352.1   | 1k/1tag      | 1.4              | 0.00/1.00              |
| 256 HGET / 8 keys, 1 hashtag / atomic                 | 200 | 393.0  | 450.8  | 543.2   | 269.0  | 690.3   | 390.9   | 78188.4   | 8k/1tag      | 1.5              | 1.00/0.00              |
| 256 HGET / 8 keys, 1 hashtag / disable-atomicity      | 200 | 7763.6 | 8197.2 | 8736.5  | 3775.5 | 10501.2 | 7629.4  | 1525877.5 | 8k/1tag      | 30.3             | 0.00/1.00              |
| 256 HGET / 8 keys, 8 hashtags / atomic                | 200 | 7014.7 | 7644.2 | 9210.8  | 3440.7 | 10868.8 | 6823.8  | 1364751.1 | 8k/8tag      | 27.4             | 0.00/1.00              |
| 256 HGET / 8 keys, 8 hashtags / disable-atomicity     | 200 | 6773.5 | 8026.5 | 11195.5 | 3569.6 | 14951.8 | 5997.6  | 1199519.2 | 8k/8tag      | 26.5             | 0.00/1.00              |
| 256 HGET / 8 keys, 1 hashtag / allow-undeclared-keys  | 200 | 7695.5 | 8261.5 | 9197.4  | 3696.5 | 10844.8 | 6869.8  | 1373959.0 | 0k decl/1tag | 30.1             | 0.00/1.00              |
| 256 HGET / 8 keys, 8 hashtags / allow-undeclared-keys | 200 | 3918.7 | 7472.2 | 7991.0  | 3326.4 | 10066.6 | 4524.8  | 904963.4  | 0k decl/8tag | 15.3             | 0.00/1.00              |
| 256 HSET (discarded) / 8 keys, 1 hashtag              | 200 | 327.2  | 364.2  | 399.1   | 287.5  | 8267.8  | 369.0   | 73796.6   | 8k/1tag      | 1.3              | 1.00/0.00              |
| 256 HSET (discarded) / 8 keys, 8 hashtags             | 200 | 7059.9 | 7831.6 | 8326.4  | 3378.1 | 11727.8 | 6782.2  | 1356449.5 | 8k/8tag      | 27.6             | 0.00/1.00              |

**`--lua_auto_async=true` (throwaway node :6382)**

| variant                                               | n   | p50_us | p95_us | p99_us  | min_us | max_us  | mean_us | total_us  | shape        | us_per_call(p50) | shardlocal/io per call |
|-------------------------------------------------------|-----|--------|--------|---------|--------|---------|---------|-----------|--------------|------------------|------------------------|
| 256 HGET / 1 key (shard-local) / atomic               | 200 | 393.6  | 838.8  | 1794.7  | 325.9  | 2982.9  | 471.6   | 94328.5   | 1k/1tag      | 1.5              | 1.00/0.00              |
| 256 HGET / 1 key (shard-local) / disable-atomicity    | 200 | 380.2  | 538.8  | 1354.7  | 280.2  | 4251.1  | 419.2   | 83848.6   | 1k/1tag      | 1.5              | 0.00/1.00              |
| 256 HGET / 8 keys, 1 hashtag / atomic                 | 200 | 6875.1 | 7224.8 | 7359.2  | 3256.0 | 7858.5  | 6538.2  | 1307643.7 | 8k/1tag      | 26.9             | 0.00/1.00              |
| 256 HGET / 8 keys, 1 hashtag / disable-atomicity      | 200 | 6887.6 | 7444.3 | 8990.8  | 3450.9 | 18228.0 | 6760.4  | 1352071.3 | 8k/1tag      | 26.9             | 0.00/1.00              |
| 256 HGET / 8 keys, 8 hashtags / atomic                | 200 | 6972.0 | 7728.9 | 8393.5  | 4945.0 | 13157.7 | 7056.7  | 1411347.6 | 8k/8tag      | 27.2             | 0.00/1.00              |
| 256 HGET / 8 keys, 8 hashtags / disable-atomicity     | 200 | 6832.9 | 8813.2 | 11513.5 | 3521.5 | 22983.6 | 6914.8  | 1382956.4 | 8k/8tag      | 26.7             | 0.00/1.00              |
| 256 HGET / 8 keys, 1 hashtag / allow-undeclared-keys  | 200 | 6957.8 | 7185.9 | 7349.7  | 3362.9 | 7508.6  | 6618.7  | 1323730.8 | 0k decl/1tag | 27.2             | 0.00/1.00              |
| 256 HGET / 8 keys, 8 hashtags / allow-undeclared-keys | 200 | 6991.5 | 7441.2 | 8213.8  | 3596.0 | 13388.5 | 6865.8  | 1373156.8 | 0k decl/8tag | 27.3             | 0.00/1.00              |
| 256 HSET (discarded) / 8 keys, 1 hashtag              | 200 | 749.4  | 819.4  | 1027.8  | 693.9  | 1250.4  | 758.0   | 151600.3  | 8k/1tag      | 2.9              | 0.00/1.00              |
| 256 HSET (discarded) / 8 keys, 8 hashtags             | 200 | 802.0  | 873.9  | 930.6   | 725.6  | 978.0   | 805.7   | 161144.6  | 8k/8tag      | 3.1              | 0.00/1.00              |

**Verdict on the 1-key vs 8-key `redis.call`.** With call count and payload held constant, one `redis.call` costs **1.01us** against 1 key, **26.95us** against 8 keys in ONE hashtag and **27.00us** against 8 keys in EIGHT hashtags. `--!df flags=disable-atomicity` on the 8-hashtag shape: 26.88us/call.

Hop microbenchmark (256 `GET` on 256 distinct keys): **25.26us/call** in one hashtag vs **23.17us/call** over 4 hashtags. The `shardlocal/io per call` column is the INFO `eval_shardlocal_coordination_total` / `eval_io_coordination_total` delta divided by the number of script invocations, i.e. which execution path each variant actually took.

**Mechanism.** The counters say the split is shard-local vs io-coordinated, and a shared hashtag does NOT by itself buy the shard-local path: on default flags the 8-keys-in-one-hashtag script reports `eval_io_coordination_total +1` per invocation, exactly like the 8-hashtag script, and costs the same per call. The hop microbenchmark agrees (25.26 vs 23.17us/call for 1 vs 4 hashtags). Only **`--lock_on_hashtags`** co-locates a hashtag on one shard: with it the one-hashtag script flips to `eval_shardlocal_coordination_total +1` and **1.54us/call** (26.95us/call without it), while the 8-hashtag script stays io-coordinated at 27.40us/call. The 1-key script is shard-local **only when it runs atomically**, and its cost differs per node: 1.01us/call on the default primary, 1.46us/call under `--lock_on_hashtags` and 1.54us/call under `--lua_auto_async` (both throwaway nodes run as a co-tenant of the default primary). With `--!df flags=disable-atomicity` the 1-key script reports `0.00/1.00` -- io-coordinated -- on all three nodes (1.23 / 1.40 / 1.49us/call), i.e. the directive takes even the 1-key script off the shard-local path.

**Fast-path killers.** `--!df flags=disable-atomicity` and `--!df flags=allow-undeclared-keys` both move the script off `eval_shardlocal_coordination_total` onto `eval_io_coordination_total` even for a single-shard script (see the counter column). Under `--lock_on_hashtags`, where there is a fast path to lose, that costs real time on the one-hashtag shape; on default flags the script was already io-coordinated, so the directives change the counter without changing the latency.

**`--lua_auto_async=true`** only affects `redis.call` whose result is discarded: the 256-`HSET`-discarded script goes 28.05 -> 2.93us/call, while every read variant is unchanged.

Raw log: [`results/q5.txt`](results/q5.txt)
<!--END q5-->

<!--BEGIN q6-->
<!--SUMMARY| Q6 | Q2 write scripts, primary with replica attached vs detached | mean p50 attached vs standalone: per-call 4679 vs 4484us (+4.3%), batched 569 vs 402us (+41.6%) | difference is beyond (per-call) / beyond (batched) the spread between replicates of the same condition |-->
### Q6 — replica attached vs standalone (Q2 write script)

200 measured iterations after 20 warm-up per cell, A's Q2 write scripts imported verbatim (`q2_write_batching.PER_CALL` / `.BATCHED`, same KEYS, same 64B values).

**How the standalone condition was produced:** the same primary process, with `REPLICAOF NO ONE` sent to `dfskill-replica` (:6380) and the primary confirmed at `connected_slaves:0` (it was `connected_slaves:1` for condition A). No restart, no flag change, identical script shas across both conditions; the replica is re-attached at the end and full sync confirmed in the raw log.

| variant                                     | n   | p50_us | p95_us | p99_us  | min_us | max_us  | mean_us | total_us | redis_calls | us_per_call(p50) |
|---------------------------------------------|-----|--------|--------|---------|--------|---------|---------|----------|-------------|------------------|
| 32x(5 HSET + 1 ZADD) | attached #1          | 200 | 4659.6 | 5485.2 | 6288.3  | 2936.5 | 9856.0  | 4752.7  | 950540.0 | 192         | 24.3             |
| 32x(5 HSET + 1 ZADD) | attached #2          | 200 | 4698.4 | 6032.2 | 10676.9 | 2710.7 | 13491.9 | 4854.7  | 970938.3 | 192         | 24.5             |
| 32x(5 HSET + 1 ZADD) | standalone #1        | 200 | 4522.0 | 6203.5 | 7549.3  | 2687.5 | 17119.3 | 4754.0  | 950805.7 | 192         | 23.6             |
| 32x(5 HSET + 1 ZADD) | standalone #2        | 200 | 4446.8 | 4987.7 | 5489.3  | 2447.7 | 5927.5  | 4484.9  | 896986.0 | 192         | 23.2             |
| 5 multi-HSET + 1 multi-ZADD | attached #1   | 200 | 505.0  | 692.5  | 858.4   | 404.2  | 1433.1  | 530.3   | 106069.6 | 6           | 84.2             |
| 5 multi-HSET + 1 multi-ZADD | attached #2   | 200 | 633.8  | 1629.8 | 2496.0  | 431.9  | 2643.8  | 776.9   | 155371.7 | 6           | 105.6            |
| 5 multi-HSET + 1 multi-ZADD | standalone #1 | 200 | 383.9  | 453.8  | 521.5   | 345.6  | 952.5   | 394.4   | 78881.6  | 6           | 64.0             |
| 5 multi-HSET + 1 multi-ZADD | standalone #2 | 200 | 420.4  | 677.9  | 1444.9  | 263.1  | 1747.4  | 452.2   | 90430.2  | 6           | 70.1             |

Two replicates per condition in ABBA order. Mean p50 with the replica attached vs standalone: per-call 4679us vs 4484us (+4.3%), batched 569us vs 402us (+41.6%). The spread between the two replicates of the SAME condition is 75us (per-call) and 129us (batched), so the attached-vs-standalone difference is **beyond** run-to-run variation for the per-call script and **beyond** it for the batched one. A positive percentage means the attached replica was slower.

Raw log: [`results/q6.txt`](results/q6.txt)
<!--END q6-->

<!--BEGIN q7-->
<!--SUMMARY| Q7 | claim_mailbox_batch.lua, 1024 due, batch 32, ~2048B records: per-candidate HGET vs chunked HMGET | p50 41183us -> 7814us (5.3x); p99 52222us -> 17150us | replies identical; prefetch + deferred payload is a 5.3x win (standalone run, no co-tenant; Q10 re-measures the same workload back to back against `--lock_on_hashtags` and reports a different ratio -- see the reconciliation note in Q10) |-->
### Q7 — real script: claim_mailbox_batch.lua vs HMGET prefetch

200 measured iterations after 20 warm-up. Before every single invocation the mutable state is reset (leases, receipts, diagnostic_owners, poll_starts, lease_records deleted; all 1024 mailboxes re-scored due in the past) so each measured call sees the identical pre-claim state; the reset is not inside the timed section. All 14 keys share the hashtag `{inbound}`.

**Fence check.** Every timed invocation was verified to return `RESOLVED` with `32/32` `CLAIMED` entries, the request tokens echoed in order and `deadline - now == 120000` - not a `FENCED`/`PROTOCOL` early exit.

**Reply equality.** On the identical seeded state with the identical 32 lease tokens, the two reply arrays are **IDENTICAL** field for field once the two server-clock fields (`now`, and `deadline`, checked instead for `deadline - now == 120000`) are normalised: same `earliest_due`, same batch size, same statuses, same mailbox ids in the same order, same ~2048B record payloads.

| variant                           | n   | p50_us  | p95_us  | p99_us  | min_us  | max_us  | mean_us | total_us  |
|-----------------------------------|-----|---------|---------|---------|---------|---------|---------|-----------|
| orig (per-candidate HGET)         | 200 | 41182.7 | 45192.2 | 52222.5 | 21131.9 | 57597.4 | 39559.6 | 7911928.3 |
| hmget prefetch + deferred payload | 200 | 7813.9  | 12082.0 | 17149.5 | 4460.3  | 21958.1 | 8346.7  | 1669349.1 |

The HMGET variant is **5.3x** faster at p50 (41183us -> 7814us), p99 52222us -> 17150us. Execution-path counters over the measured runs: orig `eval_shardlocal_coordination=+0 eval_io_coordination=+200 eval_squashed_flushes=+0 lua_blocked=+0 | lua_interpreter_return=+200 tx_normal_total=+399 tx_shard_polls=+663192 used_memory_lua=-99712`, hmget `eval_shardlocal_coordination=+0 eval_io_coordination=+200 eval_squashed_flushes=+0 lua_blocked=+0 | lua_interpreter_return=+200 tx_normal_total=+399 tx_shard_polls=+49792`.

Variant sources: `lab/variants/claim_mailbox_batch.orig.lua` (the original script) and `lab/variants/claim_mailbox_batch.hmget.lua`, whose header documents the one semantic difference (`record_missing` is only checked for the candidates actually claimed).

Raw log: [`results/q7.txt`](results/q7.txt)
<!--END q7-->

<!--BEGIN q8-->
<!--SUMMARY| Q8 | 8 clients x ~2000us script, 9th client GET p99, threads 1 vs 4, probe in same vs different hashtag | p99 t1/same 33219us, t1/diff 33084us, t4/same 169us, t4/diff 191us | see the four cells in the appendix |-->
### Q8 — head-of-line blocking (8 loaders x ~2ms script, 9th client GET)

300 probe `GET`s after 30 warm-up, issued by a 9th client while 8 separate loader PROCESSES run the ~2000us script in a tight loop (processes, not threads, so the driver's GIL cannot distort the probe). The script does N x `HGET` over 8 keys in `{q8s}`; N is calibrated per node to land near 2000us, see the raw log.

| variant                                                     | n   | p50_us  | p95_us  | p99_us  | min_us  | max_us  | mean_us | total_us  | script_us(p50) | calls/script |
|-------------------------------------------------------------|-----|---------|---------|---------|---------|---------|---------|-----------|----------------|--------------|
| threads=1 | baseline, no load, probe {q8s}probe             | 300 | 104.4   | 135.2   | 201.0   | 53.3    | 221.3   | 104.7   | 31407.3   | 1881           | 3590         |
| threads=1 | baseline, no load, probe {q8p}probe             | 300 | 66.0    | 82.0    | 147.2   | 50.1    | 184.9   | 67.5    | 20251.1   | 1881           | 3590         |
| threads=1 | 8 loaders, probe SAME hashtag ({q8s}probe)      | 300 | 29474.2 | 31408.1 | 33218.8 | 24721.7 | 34584.5 | 29630.9 | 8889257.1 | 1881           | 3590         |
| threads=1 | 8 loaders, probe DIFFERENT hashtag ({q8p}probe) | 300 | 29497.0 | 31518.3 | 33084.5 | 26920.8 | 36508.8 | 29751.8 | 8925533.2 | 1881           | 3590         |
| threads=4 | baseline, no load, probe {q8s}probe             | 300 | 113.6   | 136.0   | 197.8   | 59.1    | 246.9   | 109.0   | 32702.8   | 2043           | 64           |
| threads=4 | baseline, no load, probe {q8p}probe             | 300 | 81.5    | 104.3   | 193.2   | 68.7    | 198.7   | 85.0    | 25509.8   | 2043           | 64           |
| threads=4 | 8 loaders, probe SAME hashtag ({q8s}probe)      | 300 | 111.8   | 139.8   | 169.0   | 63.0    | 220.8   | 108.9   | 32672.5   | 2043           | 64           |
| threads=4 | 8 loaders, probe DIFFERENT hashtag ({q8p}probe) | 300 | 113.9   | 173.5   | 191.2   | 68.2    | 199.8   | 118.9   | 35676.0   | 2043           | 64           |

The four loaded cells, probe `GET` p99: threads=1 same-hashtag **33219us**, threads=1 different-hashtag **33084us**, threads=4 same-hashtag **169us**, threads=4 different-hashtag **191us** (p50 29474 / 29497 / 112 / 114us).

Read the same/different-hashtag rows with Q5 in mind: on default flags (no `--lock_on_hashtags`) a hashtag does NOT pin keys to one shard, so the two probe keys differ only by which shard their own hash lands on, not by membership of the script's shard.

Raw log: [`results/q8.txt`](results/q8.txt)
<!--END q8-->

<!--BEGIN q9-->
<!--SUMMARY| Q9 | inventory of SCRIPT\*/DEBUG/INFO/flags | 43 probes, 20 server flags | SCRIPT LATENCY+FLAGS+LIST+GC exist; SCRIPT STATS does not; FLAGS is a setter only; histograms never reset |-->
### Q9 — script command & flag inventory on df-v1.34.0

Probed on `df-v1.34.0 (redis_version 7.4.0), threads=4`.

| probe                                                                                           | verdict | first line of reply                                                            |
|-------------------------------------------------------------------------------------------------|---------|--------------------------------------------------------------------------------|
| SCRIPT HELP                                                                                     | EXISTS  | SCRIPT <subcommand> [<arg> [value] [opt] ...]                                  |
| SCRIPT LIST                                                                                     | EXISTS  | 1b03e735c03072284f716e56cf7272b87958129e                                       |
| SCRIPT EXISTS f12e2b9ff643d0eb60d9d70b840b57579495ac1b 0000000000000000000000000000000000000000 | EXISTS  | 1                                                                              |
| SCRIPT LATENCY                                                                                  | EXISTS  | 98467aef3a672cec59f8751950f6f4ccc5b02a1b                                       |
| SCRIPT GC                                                                                       | EXISTS  | OK                                                                             |
| SCRIPT FLAGS f12e2b9ff643d0eb60d9d70b840b57579495ac1b                                           | ERRORS  | ResponseError: Unknown subcommand or wrong number of arguments for 'FLAGS'. Tr |
| SCRIPT FLAGS f12e2b9ff643d0eb60d9d70b840b57579495ac1b allow-undeclared-keys                     | EXISTS  | OK                                                                             |
| SCRIPT FLAGS f12e2b9ff643d0eb60d9d70b840b57579495ac1b disable-atomicity                         | EXISTS  | OK                                                                             |
| SCRIPT FLAGS f12e2b9ff643d0eb60d9d70b840b57579495ac1b bogus-flag                                | ERRORS  | ResponseError: Invalid config format: Invalid flag: bogus-flag                 |
| SCRIPT STATS                                                                                    | ERRORS  | ResponseError: Unknown subcommand or wrong number of arguments for 'STATS'. Tr |
| DEBUG OBJHIST                                                                                   | EXISTS  | ___begin object histogram___                                                   |
| MEMORY USAGE {q9}h                                                                              | EXISTS  | 2688                                                                           |
| MEMORY USAGE <missing key>                                                                      | EXISTS  | None                                                                           |
| MEMORY DOCTOR                                                                                   | ERRORS  | ResponseError: Unknown subcommand or wrong number of arguments for 'DOCTOR'. T |
| LATENCY HISTORY                                                                                 | ERRORS  | ResponseError: Unknown subcommand or wrong number of arguments for 'HISTORY'.  |
| INFO ALL (lua_*/tx_*/eval_*/squash_*)                                                           | EXISTS  | 27 matching lines                                                              |
| dragonfly --helpfull (lua/script/lock_on/squash/interpreter)                                    | EXISTS  | 20 flags                                                                       |
| lua: global unpack defined?                                                                     | EXISTS  | function                                                                       |
| lua: table.unpack defined?                                                                      | EXISTS  | function                                                                       |
| lua: bit library present?                                                                       | EXISTS  | table                                                                          |
| lua: cjson present?                                                                             | EXISTS  | table                                                                          |
| lua: cmsgpack present?                                                                          | EXISTS  | table                                                                          |
| lua: struct present?                                                                            | EXISTS  | table                                                                          |
| lua: redis.sha1hex present?                                                                     | EXISTS  | function                                                                       |
| lua: redis.setresp present?                                                                     | EXISTS  | nil                                                                            |
| lua: redis.setresp(3) call                                                                      | ERRORS  | ResponseError: Error running script (call to dea22ec229c58145094e5999e886f0389 |
| lua: _VERSION                                                                                   | EXISTS  | Lua 5.4                                                                        |
| unpack arity N=256                                                                              | EXISTS  | 256                                                                            |
| unpack arity N=1000                                                                             | EXISTS  | 1000                                                                           |
| unpack arity N=4000                                                                             | EXISTS  | 4000                                                                           |
| unpack arity N=8000                                                                             | EXISTS  | 8000                                                                           |
| unpack arity N=8163                                                                             | EXISTS  | 8163                                                                           |
| unpack arity N=8164                                                                             | ERRORS  | ResponseError: Error running script (call to 9a1ef043ca2e53155495d97484d6a0673 |
| unpack arity N=16000                                                                            | ERRORS  | ResponseError: Error running script (call to 9a1ef043ca2e53155495d97484d6a0673 |
| SCRIPT KILL                                                                                     | ERRORS  | ResponseError: Unknown subcommand or wrong number of arguments for 'KILL'. Try |
| FUNCTION LIST                                                                                   | ERRORS  | ResponseError: Unknown subcommand or wrong number of arguments for 'LIST'. Try |
| FCALL f 0                                                                                       | ERRORS  | ResponseError: unknown command `FCALL`                                         |
| EVAL_RO                                                                                         | EXISTS  | 1                                                                              |
| EVALSHA_RO                                                                                      | ERRORS  | NoScriptError: No matching script. Please use EVAL.                            |
| directive: #!lua flags=allow-undeclared-keys reading an undeclared key                          | ERRORS  | ResponseError: user_script:2: unexpected symbol near '#'                       |
| directive: --!df flags=allow-undeclared-keys reading an undeclared key                          | EXISTS  | present                                                                        |
| directive: #!lua flags=disable-atomicity                                                        | ERRORS  | ResponseError: user_script:2: unexpected symbol near '#'                       |
| directive: --!df flags=disable-atomicity                                                        | EXISTS  | present                                                                        |

**`SCRIPT LATENCY` exists and is populated** (units: microseconds — `SCRIPT HELP` says *"Prints latency histograms in usec for every called function"*). Reply is a nested array, one `[sha, histogram-blob]` pair per script executed since server start. Verbatim block for the probe script:

```
Count: 50 Average: 39.8400  StdDev: 7.64
Min: 32.0000  Median: 38.5185  Max: 78.0000
------------------------------------------------------
[      30,      35 ) 6  12.000%  12.000% ##
[      35,      40 ) 26  52.000%  64.000% ##########
[      40,      45 ) 12  24.000%  88.000% #####
[      45,      50 ) 2   4.000%  92.000% #
[      50,      60 ) 3   6.000%  98.000% #
[      70,      80 ) 1   2.000% 100.000%
```

Parsed: SCRIPT LATENCY n=50 avg=39.84us median=38.5185us max=78.0us buckets=6

Notes:
- `SCRIPT FLAGS <sha>` with **no** flag argument errors (`ERR Unknown subcommand or wrong number of arguments for 'FLAGS'`); it is a setter, not a getter. With `allow-undeclared-keys` / `disable-atomicity` it returns `OK`, and may be called before the script is loaded.
- `SCRIPT LATENCY` histograms are **cumulative for the server lifetime** and `SCRIPT FLUSH` does **not** reset them. The harness therefore appends a run nonce comment to each script so every run gets a fresh sha (`common.unique_body`).
- `SCRIPT STATS` does not exist. `SCRIPT GC` returns `OK`.
- Server flags matching lua/script/lock_on/squash/interpreter: `--default_lua_flags`, `--interpreter_per_thread`, `--lock_on_hashtags`, `--log_squash_info_threshold_usec`, `--lua_allow_undeclared_auto_correct`, `--lua_auto_async`, `--lua_enable_redis_log`, `--lua_force_atomicity_shas`, `--lua_mem_gc_threshold`, `--lua_resp2_legacy_float`, `--lua_undeclared_keys_shas`, `--luagc`, `--max_busy_squash_usec`, `--max_squashed_cmd_num`, `--multi_eval_squash_buffer`, `--multi_exec_squash`, `--pipeline_squash`, `--pipeline_squash_limit`, `--squash_stats_latency_lower_limit`, `--squashed_reply_size_limit`.
- `--lua_auto_async` EXISTS in v1.34.0.
- Dragonfly runs **Lua 5.4** (Redis runs 5.1) and uses `--!df flags=...` on the first line; the Redis `#!lua flags=...` shebang is not Dragonfly's directive. The `lua:`, `unpack arity`, `directive:` and absent-command rows above are all live probes -- see `results/q9.txt` for the verbatim replies, including the first `unpack()` arity that fails.
- `unpack()` ceiling for `redis.call('HMGET', k, unpack(t))`: **8163 fields succeed, 8164 fails** with `@user_script:7: stack overflow`; at 16000 the error becomes `too many results to unpack`. Chunk well below this (256-1000) -- the limit is the Lua stack, not a Dragonfly setting.
- The `#!lua flags=...` shebang is not merely ignored by Dragonfly: it is a **Lua syntax error** (`user_script:2: unexpected symbol near '#'`), so a script copied from Redis fails to load. Dragonfly's directive is `--!df flags=...` on the first line, which loads and runs (probed above).
- `EVAL_RO` exists; `EVALSHA_RO` exists (returns `No matching script` for an unknown sha). `SCRIPT KILL`, `FUNCTION`, `FCALL` do not exist.
- `redis.setresp` is `nil` and calling it errors. `cjson`, `cmsgpack`, `struct`, `bit`, `redis.sha1hex`, global `unpack` and `table.unpack` are all present; `_VERSION` is **Lua 5.4** (Redis uses 5.1).

Raw log: [`results/q9.txt`](results/q9.txt)
<!--END q9-->

<!--BEGIN q10-->
<!--SUMMARY| Q10 | Q2/Q3/Q7 workloads re-run on default vs `--lock_on_hashtags`, back to back | batching 9.9x -> 2.0x; payload skip 1.3x -> 1.8x; claim_mailbox_batch hmget 7.6x -> 2.6x | see the 4-cell Q7 table and the counter split in the appendix; the flag's cost under concurrent load is in Q11(d) |-->
### Q10 — how much of each win survives under `--lock_on_hashtags`

Both nodes measured back to back in ONE session (B found Q7 strongly state-dependent, so cross-session comparison is unsound). Default node is the lab primary :6379; the `--lock_on_hashtags` node is a throwaway container on :6382 with the same production flags (`--cache_mode=false --maxmemory=2048Mi --dbfilename=dump --proactor_threads=4`), removed at the end of the run. Scripts, seeding and fences are imported verbatim from `q2_write_batching.py`, `q3_read_after_full.py` and `q7_real_script.py`. `shardlocal/io per invocation` is the INFO `eval_shardlocal_coordination_total`/`eval_io_coordination_total` delta over the timed invocations divided by their count.

**Q2 - write batching** (200 iterations after 20 warm-up)

| variant                                                     | n   | p50_us | p95_us | p99_us  | min_us | max_us  | mean_us | total_us | redis_calls | us_per_call(p50) | shardlocal/io per invocation |
|-------------------------------------------------------------|-----|--------|--------|---------|--------|---------|---------|----------|-------------|------------------|------------------------------|
| [default] 32x(5 HSET + 1 ZADD)                              | 200 | 3936.3 | 9069.3 | 13390.4 | 2764.6 | 30496.3 | 4748.2  | 949644.2 | 192         | 20.5             | 0.00/1.00                    |
| [default] 5 multi-field HSET + 1 multi-member ZADD          | 200 | 397.8  | 482.9  | 533.8   | 310.8  | 574.5   | 399.0   | 79801.3  | 6           | 66.3             | 0.00/1.00                    |
| [default] batched, redis.acall (result discarded)           | 200 | 338.7  | 402.2  | 439.4   | 271.9  | 499.0   | 342.5   | 68490.7  | 6           | 56.5             | 0.00/1.00                    |
| [lock_on_hashtags] 32x(5 HSET + 1 ZADD)                     | 200 | 214.4  | 256.2  | 282.8   | 172.4  | 340.1   | 215.2   | 43031.8  | 192         | 1.1              | 1.00/0.00                    |
| [lock_on_hashtags] 5 multi-field HSET + 1 multi-member ZADD | 200 | 109.7  | 127.9  | 150.2   | 98.8   | 192.2   | 111.6   | 22313.6  | 6           | 18.3             | 1.00/0.00                    |
| [lock_on_hashtags] batched, redis.acall (result discarded)  | 200 | 107.5  | 127.3  | 168.1   | 96.8   | 211.0   | 110.5   | 22107.8  | 6           | 17.9             | 1.00/0.00                    |

Batching wins **9.9x** on the default node and **2.0x** under `--lock_on_hashtags`. `redis.acall` on the batched writes: p50 339us (default) / 108us (lock_on_hashtags) against 398us / 110us for sync `redis.call`.

**Q3 - read-after-full vs first 32 payloads** (200 iterations after 20 warm-up)

| variant                                 | n   | p50_us  | p95_us  | p99_us   | min_us  | max_us   | mean_us | total_us   | redis_calls | us_per_call(p50) | shardlocal/io per invocation |
|-----------------------------------------|-----|---------|---------|----------|---------|----------|---------|------------|-------------|------------------|------------------------------|
| [default] payload for all 1024          | 200 | 74627.4 | 96084.4 | 105756.2 | 42584.2 | 146647.9 | 70979.9 | 14195977.2 | 3072        | 24.3             | 0.00/1.00                    |
| [default] payload for first 32          | 200 | 55423.2 | 62991.5 | 68832.8  | 29765.2 | 83058.7  | 48587.8 | 9717565.2  | 2080        | 26.6             | 0.00/1.00                    |
| [lock_on_hashtags] payload for all 1024 | 200 | 1555.9  | 1706.7  | 1798.9   | 1451.0  | 1855.5   | 1570.1  | 314022.4   | 3072        | 0.5              | 1.00/0.00                    |
| [lock_on_hashtags] payload for first 32 | 200 | 883.4   | 1032.1  | 1187.6   | 789.4   | 1993.8   | 898.6   | 179720.2   | 2080        | 0.4              | 1.00/0.00                    |

Skipping the 992 payload `HGET`s wins **1.3x** on the default node and **1.8x** under `--lock_on_hashtags`.

**Q7 - `claim_mailbox_batch.lua`, 4 cells** (200 iterations per cell after 20 warm-up; mutable state reset before every invocation, reset not timed)

| variant                                              | n   | p50_us  | p95_us   | p99_us   | min_us  | max_us   | mean_us | total_us   | shardlocal/io per invocation |
|------------------------------------------------------|-----|---------|----------|----------|---------|----------|---------|------------|------------------------------|
| [default] orig (per-candidate HGET)                  | 200 | 54947.5 | 104518.9 | 129410.5 | 47914.5 | 143868.6 | 69365.4 | 13873082.1 | 0.00/1.00                    |
| [default] hmget prefetch + deferred payload          | 200 | 7223.5  | 8147.4   | 8746.7   | 4059.2  | 17483.7  | 6444.6  | 1288916.4  | 0.00/1.00                    |
| [lock_on_hashtags] orig (per-candidate HGET)         | 200 | 2243.0  | 2540.5   | 2681.7   | 2083.5  | 2741.3   | 2287.9  | 457583.6   | 1.00/0.00                    |
| [lock_on_hashtags] hmget prefetch + deferred payload | 200 | 862.2   | 2346.4   | 4041.0   | 725.9   | 4368.0   | 1061.5  | 212306.5   | 1.00/0.00                    |

Every timed cell was fence-checked `RESOLVED` with `32/32` `CLAIMED` and `deadline - now == 120000` before being trusted. Reply arrays identical field for field (clock fields normalised): default **True**, lock_on_hashtags **True**. The HMGET variant wins **7.6x** on the default node and **2.6x** under `--lock_on_hashtags`.

**Counter split.** Q7 orig: default `eval_shardlocal_coordination=+0 eval_io_coordination=+200 eval_squashed_flushes=+0 lua_blocked=+0 | lua_interpreter_return=+200 tx_normal_total=+399 tx_shard_polls=+663192 used_memory_lua=+157440`, lock_on_hashtags `eval_shardlocal_coordination=+200 eval_io_coordination=+0 eval_squashed_flushes=+0 lua_blocked=+0 | lua_interpreter_return=+200 tx_batch_schedule_calls_total=+399 tx_batch_scheduled_items_total=+399 tx_normal_total=+399 tx_shard_optimistic_total=+200 tx_shard_polls=+995`. Q7 hmget: default `eval_shardlocal_coordination=+0 eval_io_coordination=+200 eval_squashed_flushes=+0 lua_blocked=+0 | lua_interpreter_return=+200 tx_normal_total=+399 tx_shard_polls=+49792`, lock_on_hashtags `eval_shardlocal_coordination=+200 eval_io_coordination=+0 eval_squashed_flushes=+0 lua_blocked=+0 | lua_interpreter_return=+200 tx_batch_schedule_calls_total=+399 tx_batch_scheduled_items_total=+399 tx_normal_total=+399 tx_shard_optimistic_total=+200 tx_shard_polls=+995`. Q2 per-item: default `eval_shardlocal_coordination=+0 eval_io_coordination=+220 eval_squashed_flushes=+0 lua_blocked=+0 | lua_interpreter_return=+220 tx_normal_total=+220 tx_shard_polls=+43120 used_memory_lua=-47600`, lock_on_hashtags `eval_shardlocal_coordination=+220 eval_io_coordination=+0 eval_squashed_flushes=+0 lua_blocked=+0 | lua_interpreter_return=+220 tx_inline_runs_total=+220 tx_normal_total=+220 tx_shard_optimistic_total=+220 used_memory_lua=+19952`. Q3 full: default `eval_shardlocal_coordination=+0 eval_io_coordination=+220 eval_squashed_flushes=+0 lua_blocked=+0 | lua_interpreter_return=+220 tx_normal_total=+220 tx_shard_polls=+676940 used_memory_lua=+56320`, lock_on_hashtags `eval_shardlocal_coordination=+220 eval_io_coordination=+0 eval_squashed_flushes=+0 lua_blocked=+0 | lua_interpreter_cnt=+1 lua_interpreter_return=+220 tx_inline_runs_total=+220 tx_normal_total=+220 tx_shard_optimistic_total=+220 used_memory_lua=+170864`.

**Reconciliation with the standalone Q2/Q3/Q7 runs.** The same three workloads were measured twice on default flags and the two measurements disagree: Q2 p50 5845.8us standalone vs 3936.3us in this section's default arm, Q3 34135.8 vs 74627.4us (2.2x apart), Q7 41182.7 vs 54947.5us -- so the derived speedups differ too (Q2 10.8x / 9.9x, Q3 1.1x / 1.3x, Q7 5.3x / 7.6x; standalone figures as recorded in the Q2, Q3 and Q7 sections of this report). These workloads are load-sensitive and the lab host has 4 cores: the raw log shows the throwaway `--lock_on_hashtags` container (4 busy-polling proactors) was already running when this section's default arm executed, so the default arm carries a co-tenant the standalone runs did not have. Take the RATIOS from this section -- its two arms were measured back to back under the same co-tenancy -- and the ABSOLUTE numbers from the standalone Q2/Q3/Q7 runs. Do not mix the two.

All 14 `claim_mailbox_batch` keys share the hashtag `{inbound}`, so this is exactly the shape `--lock_on_hashtags` converts from io-coordinated to shard-local; the Q2 and Q3 key sets likewise share one hashtag each.

Raw log: [`results/q10.txt`](results/q10.txt)
<!--END q10-->

<!--BEGIN q11-->
<!--SUMMARY| Q11 | pipeline vs Lua vs MULTI; 100k keys vs 1 hash; Lua limiter vs CL.THROTTLE; SCRIPT LOAD under load | 64 GET: script/pipeline p50 3.9x default, 0.4x lock_on_hashtags; Lua limiter/CL.THROTTLE 0.9x default, 0.8x lock_on_hashtags; 8894B SCRIPT LOAD p50 499us idle -> 478us under 4 loaders (cached sha 149us); 100k x 64B costs 83.7 B/value as strings vs 109.6 in one hash | see the four sub-experiments in the appendix |-->
### Q11 — does Redis performance advice hold on Dragonfly?

Four pieces of standard Redis advice, each measured on the default primary :6379 and on an otherwise identical throwaway `--lock_on_hashtags` node :6382, 200 iterations after 20 warm-up. Counter deltas in (a), (b) and (c) are for exactly the timed calls. In (d) the server is shared with 4 concurrent loader processes, so (d)'s counter deltas are server-wide and include loader traffic -- the 200 `SCRIPT LOAD`s execute no script at all, yet report `eval_*_coordination` and `lua_interpreter_return` deltas. No execution path can be inferred from (d)'s counter split.

Traceability note for THIS run: `results/q11.txt` recorded only p50/p99 per variant, so the `p95_us`, `min_us`, `max_us`, `mean_us` and `total_us` columns of the Q11 tables below are NOT raw-log-backed (the q1-q10 logs do contain their full tables). The benchmark now writes the full per-variant tables into the raw log, so a re-run backs every column.

#### a) "prefer pipelining over Lua"

64 keys in the single hashtag `{q11a}`, 2048B values, as a client pipeline on one connection (one round trip), as one atomic script, and as `MULTI`/`EXEC`.

| variant                                                      | n   | p50_us | p95_us | p99_us | min_us | max_us | mean_us | total_us | us_per_key(p50) |
|--------------------------------------------------------------|-----|--------|--------|--------|--------|--------|---------|----------|-----------------|
| default | GET x64 | client pipeline (1 round trip)           | 200 | 479.5  | 817.7  | 1305.9 | 404.1  | 3515.0 | 534.3   | 106856.0 | 7.49            |
| default | GET x64 | one atomic script                        | 200 | 1859.7 | 2139.5 | 2751.7 | 974.3  | 3609.2 | 1796.9  | 359382.7 | 29.06           |
| default | GET x64 | MULTI/EXEC                               | 200 | 1026.3 | 1206.0 | 1415.3 | 942.0  | 1546.8 | 1057.2  | 211444.1 | 16.04           |
| default | HSET x64 | client pipeline (1 round trip)          | 200 | 1419.3 | 1635.7 | 1907.2 | 1083.1 | 3509.2 | 1437.9  | 287577.1 | 22.18           |
| default | HSET x64 | one atomic script                       | 200 | 2379.0 | 2712.2 | 3584.8 | 1577.3 | 7264.0 | 2442.7  | 488538.3 | 37.17           |
| default | HSET x64 | MULTI/EXEC                              | 200 | 981.1  | 1435.6 | 1476.1 | 675.8  | 1524.0 | 1054.0  | 210798.2 | 15.33           |
| lock_on_hashtags | GET x64 | client pipeline (1 round trip)  | 200 | 445.8  | 488.9  | 538.9  | 390.1  | 574.0  | 448.1   | 89625.1  | 6.97            |
| lock_on_hashtags | GET x64 | one atomic script               | 200 | 186.3  | 211.1  | 226.1  | 163.5  | 282.0  | 188.0   | 37596.3  | 2.91            |
| lock_on_hashtags | GET x64 | MULTI/EXEC                      | 200 | 460.0  | 510.0  | 547.0  | 393.2  | 559.3  | 460.8   | 92157.5  | 7.19            |
| lock_on_hashtags | HSET x64 | client pipeline (1 round trip) | 200 | 397.3  | 437.2  | 458.2  | 339.2  | 526.7  | 398.3   | 79655.9  | 6.21            |
| lock_on_hashtags | HSET x64 | one atomic script              | 200 | 174.8  | 188.4  | 196.1  | 156.9  | 203.4  | 175.2   | 35035.8  | 2.73            |
| lock_on_hashtags | HSET x64 | MULTI/EXEC                     | 200 | 472.0  | 535.7  | 631.1  | 409.0  | 723.4  | 476.6   | 95326.8  | 7.38            |

**Counter split.** default script GET `eval_shardlocal_coordination=+0 eval_io_coordination=+220 eval_squashed_flushes=+0 lua_blocked=+0 | lua_interpreter_return=+220 tx_normal_total=+220 tx_shard_polls=+14960 used_memory_lua=+8976`; lock_on_hashtags script GET `eval_shardlocal_coordination=+220 eval_io_coordination=+0 eval_squashed_flushes=+0 lua_blocked=+0 | lua_interpreter_cnt=+1 lua_interpreter_return=+220 tx_batch_schedule_calls_total=+1 tx_batch_scheduled_items_total=+1 tx_inline_runs_total=+219 tx_normal_total=+220 tx_shard_optimistic_total=+220 used_memory_lua=+79680`; default MULTI/EXEC GET `eval_shardlocal_coordination=+0 eval_io_coordination=+0 eval_squashed_flushes=+0 lua_blocked=+0 | tx_normal_total=+220 tx_shard_polls=+1760`; default pipeline GET `eval_shardlocal_coordination=+0 eval_io_coordination=+0 eval_squashed_flushes=+0 lua_blocked=+0 | tx_batch_schedule_calls_total=+14080 tx_batch_scheduled_items_total=+14080 tx_normal_total=+14080 tx_shard_optimistic_total=+14080`.

#### b) "collapse many small keys into one hash"

100,000 values of 64B in three layouts, each on a freshly restarted server (Dragonfly's OOM check is against RSS and `FLUSHALL` does not recover it), `used_memory` delta and single-value read latency.

| layout                                      | used_memory delta (B) | B/value | build_s |
|---------------------------------------------|-----------------------|---------|---------|
| default | 100000 string keys                | 8,373,504             | 83.7    | 0.1     |
| default | 1 hash x 100000 fields            | 10,964,048            | 109.6   | 0.7     |
| default | 1000 hashes x 100 fields          | 10,960,000            | 109.6   | 0.6     |
| lock_on_hashtags | 100000 string keys       | 8,373,504             | 83.7    | 0.0     |
| lock_on_hashtags | 1 hash x 100000 fields   | 10,964,048            | 109.6   | 0.6     |
| lock_on_hashtags | 1000 hashes x 100 fields | 10,960,000            | 109.6   | 0.5     |

| variant                                                   | n   | p50_us | p95_us | p99_us | min_us | max_us | mean_us | total_us |
|-----------------------------------------------------------|-----|--------|--------|--------|--------|--------|---------|----------|
| default | 100000 string keys | single GET                 | 200 | 146.5  | 188.7  | 217.6  | 83.6   | 229.6  | 146.1   | 29226.6  |
| default | 1 hash x 100000 fields | single HGET            | 200 | 146.5  | 161.8  | 171.0  | 106.1  | 179.5  | 144.2   | 28847.4  |
| default | 1000 hashes x 100 fields | single HGET          | 200 | 148.5  | 186.5  | 213.0  | 84.1   | 274.1  | 145.1   | 29026.3  |
| lock_on_hashtags | 100000 string keys | single GET        | 200 | 188.5  | 240.6  | 263.0  | 120.3  | 293.7  | 190.4   | 38080.6  |
| lock_on_hashtags | 1 hash x 100000 fields | single HGET   | 200 | 155.5  | 178.7  | 221.2  | 102.7  | 263.3  | 154.7   | 30945.0  |
| lock_on_hashtags | 1000 hashes x 100 fields | single HGET | 200 | 148.2  | 171.8  | 233.0  | 87.8   | 245.3  | 144.8   | 28969.8  |

`DEBUG OBJHIST` output per layout is in the raw log.

#### c) "implement a rate limiter in Lua"

A classic sliding-window limiter on ONE key (`ZREMRANGEBYSCORE`+`ZCARD`+`ZADD`+`PEXPIRE`, 4 `redis.call`) against Dragonfly's native `CL.THROTTLE`.

| variant                                              | n   | p50_us | p95_us | p99_us | min_us | max_us | mean_us | total_us |
|------------------------------------------------------|-----|--------|--------|--------|--------|--------|---------|----------|
| default | Lua sliding window (4 redis.call)          | 200 | 131.3  | 150.3  | 161.2  | 106.4  | 167.3  | 133.0   | 26590.8  |
| default | CL.THROTTLE (native)                       | 200 | 150.2  | 163.7  | 172.0  | 132.6  | 243.4  | 150.9   | 30182.6  |
| lock_on_hashtags | Lua sliding window (4 redis.call) | 200 | 99.2   | 110.4  | 114.9  | 88.5   | 118.7  | 99.5    | 19905.5  |
| lock_on_hashtags | CL.THROTTLE (native)              | 200 | 118.0  | 130.5  | 134.5  | 94.5   | 140.1  | 118.4   | 23674.4  |

**Counter split.** default Lua `eval_shardlocal_coordination=+220 eval_io_coordination=+0 eval_squashed_flushes=+0 lua_blocked=+0 | lua_interpreter_cnt=+1 lua_interpreter_return=+220 tx_batch_schedule_calls_total=+220 tx_batch_scheduled_items_total=+220 tx_normal_total=+220 tx_shard_optimistic_total=+220 used_memory_lua=+43376`; lock_on_hashtags Lua `eval_shardlocal_coordination=+220 eval_io_coordination=+0 eval_squashed_flushes=+0 lua_blocked=+0 | lua_interpreter_return=+220 tx_inline_runs_total=+220 tx_normal_total=+220 tx_shard_optimistic_total=+220 used_memory_lua=-21888`.

#### d) "SCRIPT LOAD per worker is cheap"

200 sequential `SCRIPT LOAD` of the 8894B `claim_mailbox_batch.orig.lua`, idle and while 4 separate PROCESSES run a ~2000us script in a loop (q8's calibrated loader), against `EVALSHA` of a trivial 1-key script under the same load. Same text re-loads an already-cached sha; the fresh-text rows append a nonce comment so every load really compiles. The loader is calibrated per node: it landed at p50 1320us on the default node and 2034us under `--lock_on_hashtags` (target 2000us, see the raw log), so the two nodes ran at ~1.5x different load -- the cells are NOT equal-load across nodes.

| variant                                                                          | n   | p50_us | p95_us | p99_us | min_us | max_us  | mean_us | total_us  | loader script us |
|----------------------------------------------------------------------------------|-----|--------|--------|--------|--------|---------|---------|-----------|------------------|
| default | idle | SCRIPT LOAD 8894B, same text (sha already cached)               | 200 | 161.7  | 209.8  | 231.1  | 117.5  | 311.8   | 164.4   | 32880.3   | 1320             |
| default | idle | SCRIPT LOAD 8894B, fresh text each time                         | 200 | 498.7  | 569.5  | 647.6  | 421.5  | 690.1   | 496.2   | 99235.0   | 1320             |
| default | idle | EVALSHA of a 1-key trivial script                               | 200 | 115.2  | 152.3  | 186.5  | 62.3   | 212.0   | 110.0   | 22004.7   | 1320             |
| default | 4 loaders | SCRIPT LOAD 8894B, same text (sha already cached)          | 200 | 149.0  | 198.7  | 222.5  | 93.3   | 485.8   | 149.4   | 29876.2   | 1320             |
| default | 4 loaders | SCRIPT LOAD 8894B, fresh text each time                    | 200 | 478.0  | 737.1  | 1331.9 | 318.2  | 1984.5  | 531.5   | 106307.1  | 1320             |
| default | 4 loaders | EVALSHA of a 1-key trivial script                          | 200 | 81.2   | 109.9  | 113.5  | 58.2   | 124.1   | 83.9    | 16782.0   | 1320             |
| lock_on_hashtags | idle | SCRIPT LOAD 8894B, same text (sha already cached)      | 200 | 132.8  | 141.0  | 146.2  | 108.6  | 147.7   | 133.0   | 26603.6   | 2034             |
| lock_on_hashtags | idle | SCRIPT LOAD 8894B, fresh text each time                | 200 | 439.5  | 617.5  | 795.8  | 374.7  | 1654.8  | 467.1   | 93418.7   | 2034             |
| lock_on_hashtags | idle | EVALSHA of a 1-key trivial script                      | 200 | 124.6  | 137.9  | 160.4  | 79.3   | 173.9   | 124.8   | 24958.0   | 2034             |
| lock_on_hashtags | 4 loaders | SCRIPT LOAD 8894B, same text (sha already cached) | 200 | 162.0  | 233.0  | 256.2  | 121.9  | 293.8   | 169.4   | 33884.3   | 2034             |
| lock_on_hashtags | 4 loaders | SCRIPT LOAD 8894B, fresh text each time           | 200 | 7787.0 | 8484.6 | 8665.6 | 5717.3 | 10732.5 | 7861.2  | 1572231.2 | 2034             |
| lock_on_hashtags | 4 loaders | EVALSHA of a 1-key trivial script                 | 200 | 7712.4 | 8179.2 | 8620.7 | 3142.3 | 13027.2 | 7757.5  | 1551501.7 | 2034             |

**Counter split under load.** default SCRIPT LOAD 8894B, same text (sha already cached): `eval_shardlocal_coordination=+0 eval_io_coordination=+20 eval_squashed_flushes=+0 lua_blocked=+0 | lua_interpreter_cnt=+1 lua_interpreter_return=+240 tx_batch_schedule_calls_total=+220 tx_batch_scheduled_items_total=+220 tx_normal_total=+240 tx_shard_optimistic_total=+220 tx_shard_polls=+1613 used_memory_lua=-1606776`; default SCRIPT LOAD 8894B, fresh text each time: `eval_shardlocal_coordination=+0 eval_io_coordination=+30 eval_squashed_flushes=+0 lua_blocked=+0 | lua_interpreter_return=+251 tx_batch_schedule_calls_total=+220 tx_batch_scheduled_items_total=+220 tx_normal_total=+250 tx_shard_optimistic_total=+220 tx_shard_polls=+2430 used_memory_lua=+2690320`; default EVALSHA of a 1-key trivial script: `eval_shardlocal_coordination=+220 eval_io_coordination=+14 eval_squashed_flushes=+0 lua_blocked=+0 | lua_interpreter_return=+235 tx_batch_schedule_calls_total=+220 tx_batch_scheduled_items_total=+220 tx_normal_total=+234 tx_shard_optimistic_total=+220 tx_shard_polls=+1169 used_memory_lua=+849616`; lock_on_hashtags SCRIPT LOAD 8894B, same text (sha already cached): `eval_shardlocal_coordination=+32 eval_io_coordination=+0 eval_squashed_flushes=+0 lua_blocked=+0 | lua_interpreter_return=+252 tx_batch_schedule_calls_total=+220 tx_batch_scheduled_items_total=+220 tx_inline_runs_total=+32 tx_normal_total=+252 tx_shard_optimistic_total=+252 used_memory_lua=-15280`; lock_on_hashtags SCRIPT LOAD 8894B, fresh text each time: `eval_shardlocal_coordination=+896 eval_io_coordination=+0 eval_squashed_flushes=+0 lua_blocked=+0 | lua_interpreter_return=+1116 tx_batch_schedule_calls_total=+220 tx_batch_scheduled_items_total=+220 tx_inline_runs_total=+896 tx_normal_total=+1116 tx_shard_optimistic_total=+1116 used_memory_lua=+1280560`; lock_on_hashtags EVALSHA of a 1-key trivial script: `eval_shardlocal_coordination=+1116 eval_io_coordination=+0 eval_squashed_flushes=+0 lua_blocked=+0 | lua_interpreter_return=+1116 tx_batch_schedule_calls_total=+220 tx_batch_scheduled_items_total=+220 tx_inline_runs_total=+896 tx_normal_total=+1116 tx_shard_optimistic_total=+1116 used_memory_lua=+24256`

**Verdicts.** (a) one atomic script vs one pipeline round trip: 3.9x on default flags, 0.4x under `--lock_on_hashtags` (writes: 1.7x / 0.4x). (c) the Lua limiter costs 0.9x `CL.THROTTLE` on default flags and 0.8x under `--lock_on_hashtags`. (d) a fresh-text `SCRIPT LOAD` costs p50 499us idle and 478us under 4 loaders on default flags, 440us -> 7787us under `--lock_on_hashtags`; re-loading an already-cached sha costs 149us / 162us under the same load.

**`--lock_on_hashtags` reintroduces head-of-line blocking.** Under 4 loaders, `EVALSHA` of a trivial 1-key script that touches none of the loader's keys costs p50 7712.4us on the lock_on_hashtags node against 81.2us on the default node (95x), while idle both nodes serve it in 115-125us. The flag buys the shard-local path (Q5, Q10) and pays for it by serialising unrelated work behind the loaders on the shared-tag shard. Q8's reassuring `proactor_threads=4` result was measured on default flags only, so it does not cover this case.

Raw log: [`results/q11.txt`](results/q11.txt)
<!--END q11-->
