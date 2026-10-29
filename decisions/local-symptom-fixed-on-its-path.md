# A local symptom is fixed on its path, not by a shared-mechanism lever

## Decision

A symptom on one path is fixed on that path (the statement's work, the rows it reads, a precomputed source, the burst's own cost). A lever on a mechanism every consumer of a shared store passes through (scheduling, priorities, concurrency limits, server configuration) is not a fix for a local symptom. The order of a shared system follows the data: work that other consumers depend on (ingestion, recompute) goes before the readers of its output; a slow reader is made cheaper, not promoted; an order that puts readers over their producers is a defect to revert, whoever installed it. Any change to a shared mechanism answers "suppose it is built: what new bottleneck appears, where does it break for every consumer, who gets worse", with consumers named, in the design and again in review.

Placement:
- rules/always-on.md, Scope: the bullet (measured text, verbatim); the Measurement bullet's "one level up" is limited to the request or the task's own path.
- merge-gate: a fourth required field `WORSE_FOR:` (orch/tools/gate_policy.py), presence-only like the other three.
- orch reviewer Altitude lens, orchestrate step 2 design review, builder design note: the same question; a review verdict without it is not MERGEABLE.

## Why

A store-wide CPU scheduler was merged while the owner was away to relieve a UI p95 symptom that existed only during a four-minute background burst on one page path. Eleven days later the scheduler stopped granting slots and the production store was down for hours (incident N-04).

Owner, 2026-10-29: levers with harmful effects on the whole system were used to treat a symptom instead of the problem; "background first, then UI" is the right priority, a slow UI is optimized, not promoted; no reviewer asked "suppose we built it, what new bottlenecks appear and where can it break, won't it get worse?".

## Compared against

- First formulation "stall signal + way back + vendor maturity": rejected by the owner 2026-10-29. It mitigates a shared lever instead of asking whether a local symptom licenses one; #237 had a way back and still took the store down.
- Rule text alone: the owner chose text plus a merge-gate field so the question is unavoidable at the merge.
- "The existing priority order of a shared system is a contract" (first applied wording): rejected by the owner the same day. It freezes whatever is configured, including a wrong order just installed (after #237 the scheduler itself would have been "the contract"). "Set by the owner" was rejected too: the agent cannot know who the owner is or act when nobody is there. The order is derived from the data dependency instead, so it is decidable from one read and a wrong order is reverted without anyone's word.

## Measured

retro-evals runs/lever-level, k=3: CASE-50 0/3 -> 3/3, CASE-50B 1/3 -> 3/3 with the bullet. Controls held: CASE-41, CASE-26 and CASE-5 0/3 -> 3/3 (baselines re-graded), CASE-7 3/3 -> 3/3 with median reply 5447 -> 4733 chars; accepted by the pre-registered rule (retro-evals runs/lever-level/RESULT.md). The Measurement-bullet clause and the gate field are not measured.
