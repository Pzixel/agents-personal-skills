# iteration-1 — does `dragonfly-scripting` get used, and does it help?

6 runs: 3 evals x {with_skill, without_skill}, all `implementer`/Opus, same tools, prompts verbatim from `evals/evals.json`.
Assertions went into `eval_metadata.json` before any grading. One `reviewer`/Opus grader per eval saw both arms.

## Pass rates
| eval | with_skill | without_skill |
|---|---|---|
| 1 optimize-claim-batch | 6/7 = 0.86 | 6/7 = 0.86 |
| 2 review-apply-page | 8/8 = 1.00 | 6/8 = 0.75 |
| 3 design-new-op | 8/8 = 1.00 | 5/8 = 0.63 |
| **aggregate** | **95.3%** | **74.7%** (delta +0.21) |

## The skill is used (usage.json per with-skill run)
All three with-skill runs read SKILL.md. eval 1: execution-model + lua-patterns, ran `scripts/bench_script.py` and
`scripts/lab.sh`, reused `assets/examples/claim_mailbox_batch_{seed,spec}`. eval 2: execution-model + lua-patterns,
static only. eval 3: lua-patterns + measurements, ran `scripts/lab.sh`, 33 server interactions. No baseline transcript
contains any path under `dragonfly-scripting/` (grep count 0), so the arms are clean.

## Measured latency, eval 1 — grader-run, one container, shipped spec+seed, `--reseed`, 30 iters
| script | p50 |
|---|---|
| original (paired with A) | 100 114 us |
| original (paired with B) | 41 852 us |
| with_skill | 6 424 us (5 995 head-to-head) |
| without_skill | 6 046 us (6 284 head-to-head) |
`bench_script.py --compare` exit 0 for both arms vs the original, and for A vs B. The two optimized scripts are
statistically indistinguishable. **Eval 1 does not discriminate on speed** — both arms find the batching win.

## What actually discriminated: evidence discipline, not correctness
- **Placement.** Both baselines asserted a shared `{hashtag}` co-locates keys on one shard. It does not on default
  flags. Both with-skill arms measured `eval_io_coordination_total +1 / eval_shardlocal +0`. This one wrong premise
  sank eval 2 #6 and eval 3 #7/#8.
- **Measurement method.** With-skill: identical-seed `--compare`, p50+p99, SCRIPT LATENCY / coordination counters.
  Baselines: `redis-benchmark -r 100000` (arms never see identical state), `INFO commandstats` only.
- **Redis-vs-Dragonfly.** Baseline eval 3 cited Redis 7 `#!lua flags=` as the Dragonfly mechanism; with-skill used
  `--!df flags=` and noted `no-writes` is parsed and ignored. Baseline eval 2 priced hops at a guessed 3-6 us (~5x low)
  and made Lua 5.1 claims about a Lua 5.4 engine.

## Non-discriminating assertions — fix before iteration 2
- Eval 1 is fully non-discriminating (6/7 vs 6/7, identical failures). Its #4 `payload-reads-bounded-by-batch-size` is
  **unsound**: bounding the record read changes replies, because the original returns `PROTOCOL record_missing` for
  eligible candidates past the 32nd. It contradicts #6 and the prompt. Drop or restate it.
- Eval 1 #7 passes by omission; rephrase positively ("must cite a Dragonfly execution-path counter") to separate arms.
- No eval-1 assertion checks post-state, only replies; the with-skill arm reorders all HDEL before all HSET and argues
  the colliding-token input is unsupported, while the baseline guards it with `written_slot`. That is a real
  difference no assertion sees.
- Eval 3 #1,2,3,5,6 and eval 2 #1,2,3,4,5,8 passed on both arms. Batching and line citation are not where the skill adds
  value. Eval 3 #3 accepts `call` or `acall`, so it misses the buffered-write difference with-skill actually made.
- Nothing covered redundant writes (`:612-616` delete-and-reinsert of unchanged grants, `:403-415` rewriting records
  already proven identical) — the one place the **unskilled** arm found more. Add it.

## Concrete changes the skill needs
1. `references/measurements.md` and SKILL.md 4.4 are wrong: on v1.34.0 `SCRIPT FLUSH` did **not** reset an existing
   sha's `SCRIPT LATENCY` histogram. Tell the reader to difference before/after or restart the node.
2. Promote "a shared hashtag does not co-locate without `--lock_on_hashtags`" to the front page: both baselines got it
   backwards and it is the skill's highest-value single fact.
3. Add a redundant-write-elimination rule (prove-equal-then-write-anyway costs journal and replica bytes).
4. Make `--compare` + `--reseed` the front-page instruction for any before/after claim.
5. Batching alone is table stakes for Opus; the skill's measurable edge is the execution-model and measurement
   references. Iteration 2 should test where Opus is *wrong* unaided, not where it is merely slower.
