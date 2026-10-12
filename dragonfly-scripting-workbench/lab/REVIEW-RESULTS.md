# Review of lab/RESULTS.md — numerical sanity and traceability

**Verdict: SOUND WITH CAVEATS.** The measurement arithmetic is clean: no ordering
violations anywhere, every ratio recomputes, every q1–q10 table number appears
verbatim in its raw log, and all eleven questions are present in both the summary
table and the appendix. Three claims, however, are labelled or generalised in a way
that would become wrong advice, and Q10's "default" baseline is not comparable to
the Q2/Q3/Q7 runs it is contrasted with.

## Blocking

- `lab/RESULTS.md:13` (and the identical marker at `:27`): **high**: Q1's key number
  and conclusion, "0.67us per redis.call" / "each redis.call costs ~0.67us", carry no
  key-shape qualifier. `lab/results/q1.txt:4` shows the workload is `one hash {q1}h` —
  a single key, i.e. the shard-local path. Q5 measures the *same* 256-call shape over
  8 keys at **26.95us/call** (`RESULTS.md:129`) and states the headline that a shared
  hashtag does not buy that path. A skill that quotes Q1's 0.67us as the cost of
  `redis.call` is wrong by ~40x for any multi-key script. Fix: label the Q1 row
  "0.67us per redis.call **against one key (shard-local)**" and cross-reference Q5.
- `lab/RESULTS.md:16` (and `:90`): **high**: Q4's conclusion "a multi-MiB plan blob
  blocks the shard for **milliseconds**" is not supported by any number in Q4. The
  worst concurrent-`GET` p99 in the same row is **375.1us** (`RESULTS.md:51`,
  `results/q4.txt:51`) — sub-millisecond — and the 32 MiB `max_us` of 19927.4 is
  *lower* than the idle baseline's `max_us` of 29586.2 (`RESULTS.md:48`), so the max
  column does not support blocking either. Fix: restate the conclusion as what was
  measured (p99 375us vs 155us idle) and move the millisecond claim to Q8, which
  measured it under control.
- `lab/RESULTS.md:117`: **high**: the only figure that would support the millisecond
  claim — "an earlier run ... `GET` p50 13875us / p99 30503us over only 43 samples" —
  has **no raw-log source**. Neither 13875, 30503 nor 43 occurs anywhere in
  `results/q4.txt`, and no other results file is cited. Same for the "other runs show
  p99 ~130us over 10k samples" in the same sentence. Fix: drop the numbers or add the
  run's log to `results/`.
- `lab/RESULTS.md:184`: **high**: "The 1-key script is shard-local under every flag
  combination at ~1.01us/call" is contradicted by Q5's own tables. The
  `1 key / disable-atomicity` rows report `shardlocal/io = 0.00/1.00` (io-coordinated)
  on **all three** nodes — `:128` (default, 1.2us/call), `:149` (lock_on_hashtags,
  1.4us/call), `:170` (lua_auto_async, 1.5us/call) — and the 1-key *atomic* rows on
  the two throwaway nodes are 1.5us/call, not 1.01. The 1.01 figure is the default
  node's atomic cell only. Fix: "shard-local on default flags when atomic; the
  `disable-atomicity` directive moves even the 1-key script onto io-coordination"
  (which is what the Fast-path-killers paragraph at `:186` already says correctly).

## Should fix

- `lab/RESULTS.md:14,15,19` vs `:22`: **medium**: the summary quotes two different
  default-node measurements of the same three workloads without cross-reference.
  Q3 p50 34135.8us (`:82`) vs Q10 default 74627.4us (`:387`) — **2.2x apart**;
  Q7 41182.7us (`:233`) vs Q10 default 54947.5us (`:394`); Q2 5845.8us (`:64`) vs
  Q10 default 3936.3us (`:378`). The derived speedups therefore disagree: Q3 "1.1x"
  vs Q10 default "1.3x", Q7 "5.3x" vs "7.6x", Q2 "10.8x" vs "9.9x". A reader picking
  a number for advice has no rule for which run to trust. Contributing cause:
  `results/q10.txt:10` shows the throwaway `--lock_on_hashtags` container was started
  on the same 4-core host *before* the default-node arm ran, so Q10's "default" arm
  carries a second Dragonfly (4 busy-polling proactors) as co-tenant. Fix: state in
  the summary which run each ratio comes from, and note that Q10's default arm is a
  within-session control, not comparable to Q2/Q3/Q7's standalone numbers.
- `lab/RESULTS.md:398`: **medium**: "Counter deltas are for exactly the timed calls"
  is false for sub-experiment (d). 200 `SCRIPT LOAD` calls execute no script, yet the
  rows at `:477`-area report `eval_io_coordination=+20` / `lua_interpreter_return=+240`
  (default, cached sha) and `eval_shardlocal_coordination=+896` /
  `lua_interpreter_return=+1116` (lock_on_hashtags, fresh text). Those deltas are the
  4 concurrent loader processes. Any mechanism inferred from Q11(d)'s counter split is
  contaminated. Fix: scope the sentence to (a)/(c), or mark (d)'s counters as
  server-wide including loader traffic.
- `lab/RESULTS.md:460`: **medium**: the header says the load is "4 separate PROCESSES
  run a ~2000us script", but the table's own `loader script us` column says **1320**
  on the default node, and `results/q11.txt:31` records the calibration explicitly:
  "chose n=76 -> p50 1320us (target 2000us)". The default-flag idle-vs-load comparison
  (499us -> 478us) is therefore at ~2/3 of the stated load, while the
  lock_on_hashtags arm ran at 2034us. The two nodes' load levels differ by 1.5x, which
  is part of the 440us -> 7787us contrast. Fix: state both loader costs in the verdict.
- `lab/RESULTS.md:464-475` (and `:404-415`, `:434-441`, `:449-454`): **medium**:
  Q11 traceability gap. `results/q11.txt` records only `p50`/`p99` per variant; the
  `p95_us`, `min_us`, `max_us`, `mean_us`, `total_us` columns of every Q11 table exist
  nowhere in the raw log. (Everything the log *does* record matches the appendix to
  within the log's integer rounding; q1–q10 raw logs contain the full tables.)
  Fix: emit the full per-variant table into q11.txt on the next regeneration.
- `lab/RESULTS.md:475` vs `:469`: **medium**: unremarked contradiction with the
  report's pro-`lock_on_hashtags` story. Under 4 loaders, `EVALSHA` of a **trivial
  1-key script** costs p50 **7712.4us** on the lock_on_hashtags node vs **81.2us** on
  the default node — a 95x head-of-line penalty on the flag Q5/Q10 otherwise
  recommend. The verdict at `:477` mentions only `SCRIPT LOAD`. Q8's reassuring
  "threads=4, no blocking, ~112us" result (`:251-254`) was measured on default flags
  only. Fix: add one sentence — `--lock_on_hashtags` buys the shard-local path and
  reintroduces head-of-line blocking on the shared-tag shard.
- `lab/RESULTS.md:34` vs `:13`: **low**: Q1's own N=32 cell gives **5.1us/redis.call**
  and N=256 gives 1.0; the quoted 0.67 is the N=8192 cell only. Fixed per-invocation
  overhead dominates below ~256 calls. Quoting 0.67 bare overstates the batching
  payoff for small N. Fix: quote a range ("5.1us at N=32 down to 0.67us at N=8192").

## Notes

- `lab/RESULTS.md:212`: Q6's batched effect (569 - 402 = 167us) exceeds the
  within-condition spread (129us) by only 1.3x, with 2 replicates per condition.
  "Beyond run-to-run variation" is a fair reading for the per-call arm (195us vs 75us)
  but thin for the batched arm. No number is wrong; the strength of the claim is.
- `lab/RESULTS.md:47-52`: Q4's concurrent-`GET` cells compare p99 across sample counts
  of 1055 / 12010 / 28971 / 29467 taken over very different wall-clock windows. The
  appendix already calls these unstable (`:117`); the summary row at `:16` does not.
- `lab/RESULTS.md:181`: the Q5 verdict heading says "the 0.6us vs 29us redis.call", but
  neither 0.6 nor 29 appears in Q5's tables (measured: 1.01 and 26.95/27.00). 0.6 is
  Q1's single-key figure. Cosmetic in the appendix, but it is the sentence most likely
  to be lifted into the skill.
- `lab/RESULTS.md:246-254`: Q8 is the cleanest support for the headline story and the
  report leaves it implicit. The threads=1 node runs 3590 calls in 1881us
  (**0.52us/call**, one shard, everything shard-local) while threads=4 runs 64 calls in
  2043us (**31.9us/call**, io-coordinated). Worth stating explicitly — it is an
  independent confirmation of Q5's 1us vs 27us split from a different driver.

## Checks that passed (stated so they are not re-litigated)

- **All eleven questions present**, in both the summary table and the appendix; the
  `<!--SUMMARY|...-->` marker inside each `<!--BEGIN qN-->` block is byte-identical to
  the corresponding summary-table row for all of Q1..Q11. The historical `_FRAG_RE`
  `q\d` bug (which would have dropped q10/q11) is **not** present in this artifact.
- **No impossible orderings** in any of the ~85 latency rows: p50<=p95<=p99,
  min<=p50, max>=p99, mean within [min,max], no zero/negative latencies.
- **total_us == mean_us x n** within 0.5% for every row that carries both.
- **Units**: every latency column is suffixed `_us` and every inline figure is `us`.
  Q4's "1905us at 1 MiB" matches the 1904.6 table cell; Q9's `SCRIPT LATENCY`
  histogram is explicitly confirmed microseconds from `SCRIPT HELP`. No ms/us mixing.
- **Ratios recompute** from the same question's table: Q1 5496.4/4698.0=1.17 ("1.2x"),
  Q2 5845.8/539.4=10.84 ("10.8x"), Q3 34135.8/31971.2=1.07 ("1.1x"),
  Q7 41182.7/7813.9=5.27 ("5.3x"), Q10 3936.3/397.8=9.89 / 214.4/109.7=1.95
  ("9.9x -> 2.0x"), 74627.4/55423.2=1.35 / 1555.9/883.4=1.76 ("1.3x -> 1.8x"),
  54947.5/7223.5=7.61 / 2243.0/862.2=2.60 ("7.6x -> 2.6x"),
  Q11a 1859.7/479.5=3.88 ("3.9x"), 186.3/445.8=0.42 ("0.4x"),
  Q11c 131.3/150.2=0.87 ("0.9x"), 99.2/118.0=0.84 ("0.8x"),
  Q11b 8373504/100000=83.7 and 10964048/100000=109.6.
  Q6's +4.3% / +41.6% and the 75us / 129us within-condition spreads also recompute.
- **n**: 200 measured after 20 warm-up everywhere, 300 after 30 in Q8, and Q4 states
  its reduced n=50/20 with the reason; the two n=0 rows are labelled with the OOM
  `ResponseError`. No table omits n.
- **Raw-log traceability**: every numeric cell in the q1..q10 appendix tables occurs
  verbatim in the corresponding `results/qN.txt`. Q5's `shardlocal/io per call` column
  matches `results/q5.txt` ("counters over 220 invocations", 200 measured + 20 warm-up,
  ratio unaffected). Q10's counter split and Q7's counter strings are verbatim.
- **Headline consistency**: Q5 (26.95 vs 1.01), Q10 (20.5/24.3 io vs 1.1/0.5
  shard-local), Q11a (29.06us/key io vs 2.91 shard-local), Q11c (1-key limiter,
  `eval_shardlocal_coordination=+220`) and Q8 (31.9 vs 0.52us/call) all agree with the
  central claim. Q1 is the only table that appears to contradict it, and only because
  its single-key shape is unlabelled (blocking finding 1).
