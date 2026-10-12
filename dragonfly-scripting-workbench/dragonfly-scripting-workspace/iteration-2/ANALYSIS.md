# iteration-2 — analysis

Evals 1 and 2 rerun from scratch (both arms, fresh Opus implementers, same prompts, revised assertions). Eval 3 is the iteration-1 directory copied unchanged (phase-4 decision 4).

| eval | with (it-2) | without (it-2) | with (it-1) | without (it-1) |
|---|---|---|---|---|
| 1 optimize-claim-batch | 8/8 = 1.000 | 6/8 = 0.750 | 6/7 = 0.857 | 6/7 = 0.857 |
| 2 review-apply-page | 9/9 = 1.000 | 6/9 = 0.667 | 8/8 = 1.000 | 6/8 = 0.750 |
| 3 design-new-op (copy) | 8/8 = 1.000 | 5/8 = 0.625 | 8/8 = 1.000 | 5/8 = 0.625 |
| **total** | **25/25 = 100%** | **17/25 = 68%** | 22/23 = 95.3% | 17/23 = 73.9% |

`benchmark.md`: with 100% ± 0%, without 68% ± 6%, delta +0.32; tokens 59773 ± 8694 vs 48053 ± 12443.

## New eval-1 assertions: both discriminate — and they are the only two that split the arms
- `states-execution-regime-with-evidence`: with-skill named the io-coordinated regime, its cause (shared hashtag does not co-locate without `--lock_on_hashtags`) and measured `eval_io_coordination_total +1.00`/invocation; without-skill has zero matches for `coordination|io_coord|shardlocal|hashtag|lock_on`.
- `before-after-on-identical-seeded-state`: without-skill reseeded and checked reply equality but reported p50/mean/min/max only — `p99` appears nowhere.
- Deleting `payload-reads-bounded-by-batch-size` was right: both arms kept the whole-window `PROTOCOL record_missing` scan, which that assertion would have punished.
- Non-discriminating: eval-1 assertions 1–5 and `no-unverified-redis-only-advice` (the baseline measured its own per-call figure); eval-2's new `flags-redundant-writes` (both arms found `:612-616` and `:403-415`).
- Caveat: the two new assertions test reporting discipline, not outcome quality — the baseline script is as fast and as correct as the with-skill one. In eval 2 the baseline review was substantively wrong (asserted hashtag co-location on default flags; four findings rest on it) and only assertion 6 caught it.

## Grader re-measurement (one fresh container, df-v1.34.0, `--proactor_threads=4`, 30 iters + 5 warmup, `--reseed --compare`)

| run | script | p50 µs | p99 µs | SCRIPT LATENCY avg µs |
|---|---|---|---|---|
| A | original | 99270.6 | 153354.5 | 96029.7 |
| A | with_skill | 7446.3 | 10883.2 | 5794.1 |
| B | original | 68480.6 | 71497.2 | 67381.5 |
| B | without_skill | 6749.9 | 11164.3 | 5360.9 |
| C control | original vs original | 97735.9 / 97756.0 | 119023.8 / 130951.9 | ratio 1.000 |

All runs printed `replies identical on this seed (164 fields, ignored [1, 7, 12, …162])`; coordination was `shardlocal=0.00 io=1.00 -> cross-shard` every time. The original's p50 drifts 68–99 ms between runs, so the arms must not be compared across runs; both land at ~7 ms, 10x beyond that drift.

## Limitations
- Reply-only checking (decision 2): `--compare` is the contract, no post-state dump is required; both arms added state-dump differentials voluntarily.
- The skill text was edited immediately before these runs (commit `944d4d2`) and the description loop rewrites the `description:` frontmatter concurrently; with-skill arms were pointed at the skill path explicitly.
- eval-2 `timing.json` was captured by the main session from notifications of runs started by the killed phase-4 lead and could not be re-verified here; eval-1 timing is from this lead's notifications; eval-3 timing is inherited from iteration 1. None is wall-clock isolated. One run per arm: no within-eval variance.
