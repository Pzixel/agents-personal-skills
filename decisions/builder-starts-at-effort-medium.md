# The orchestration builder starts every task at effort medium

## Decision

`orch:builder` runs at effort `medium` from its first delivery (`orch/agents/builder.md`). The respawn after a check-missed BLOCKER no longer raises it.

## Why

Measured 2026-10-28..2026-11-11 on product-repository transcripts, with effort low as the definition's value, assumed for spawns that set none (most of them; a few first runs already ran at medium): a reviewer-found BLOCKER missed by the declared check on the first delivery in 25 % of 8 small, 47 % of 113 medium and 72 % of 57 large merged tasks.

Delivery-step replay of 5 past tasks, k=4, blind grades: the missed defect named in 13 of 20 runs at medium against 4 of 20 at low, a run costing $0.342 against $0.255. A from-scratch replay pilot measured medium at 1.7x the cost of low per builder run.

## Compared against

"Resolves" below means the 95 % half-width of the difference in shares.

- Keep low: rejected by the shares above.
- A 50/50 split by workspace hash for a week: about 32 medium-tier and 20 large-tier merged tasks per arm resolve 19 points pooled over tiers, 25-27 per tier, and no cost difference.
- A full replay of 120 runs: the replay has no design-review input, did not reproduce the original defect on the one defective pilot task, and 120 runs resolve about 20 points pooled and 35-40 within one tier.
- Requiring the builder to map each acceptance criterion to its proving run: 3 of 10 against 3 of 10, no effect.

## What may get worse

Medium used about 1.6x the output tokens and more turns in the pilot, so the 220k-token context stop and its handoff respawns may become more frequent; watch the recount's per-task count of context respawns. The $25 builder cost ceiling is not reached at 1.7x by any measured task (per-task means).

## Re-measurement and undo

The landing time is the commit time of this file on the default branch (`git log -1 --format=%cI -- decisions/builder-starts-at-effort-medium.md`). In the private eval notes run `tmp/orch-cost/task_cost.py 7` one week after landing and `tmp/orch-cost/task_cost.py 14` not earlier than 14 days after landing, counting only tasks first sent to the builder after landing. The periods are told apart by the landing time only, because a spawn without an explicit effort leaves no effort mark in the transcript. Compare missed-BLOCKER share and cost per merged task per tier with `tmp/orch-cost/TASK-COST.md`. Pooled over tiers, before/after resolves about 12 points after one week and 10 after two; per tier after one week, about 15 for medium and 18 for large.

Known limit: the baseline is an earlier period, not concurrent.

Undo: revert the commit adding this file.
