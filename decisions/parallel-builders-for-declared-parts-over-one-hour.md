# Parallel builders for declared parts of a task over one hour

## Decision

Before the step 1 spawn the main session writes the task as parts (each built, reviewed and merged on its own), what each part comes after, and the whole task's estimate in minutes as if the parts were built one after another. When that estimate is over `parallel.split_above_minutes` (orchestrate-params.json; the owner's number, 60), every part with no dependency gets its own worktree, branch and `orch:builder`, all spawned in one message; a dependent part starts when what it comes after has landed. Two parts that edit one file depend on each other. Text: orchestrate SKILL.md, section Parts.

Every non-handoff builder prompt carries one line `parts: <N> min; <name>[ after <name>, ...]; ...; mine: <name>[, <name>]`; several owned names are right only at or under the threshold, where the task goes to one builder as before. The brief gate (`gate_policy.parts_decision`) refuses a prompt without a parsable line, one whose `mine:` names an undeclared part, and one that owns more than one part while N is over the threshold. Nothing else is enforced: `whole`, a chain, or a low N pass. A prompt with any line starting `handoff:` is exempt (tasks in flight; about two in five builder spawns are handoffs, and real re-spawns put that line before, between or after the other keys). Known MINOR, accepted: a `handoff:` line quoted inside the task text exempts a first spawn too.

## Why the text carries it and the gate only keeps the line present

Bench of fresh headless sessions on a synthetic feature of six plan items with dependencies not stated (builder spawns recorded, not executed): before the change 2 of 2 sessions that spawned gave the feature to one builder; with the Parts paragraph as text and no gate, 3 of 3 wrote the line and spawned four builders in one message, holding the one no-dependency part that waited on the owner. With independence stated in the prompt, 3 of 3 launched in parallel under the old rules. The defect was the missing declaration, not the launch. The threshold is compared with the whole task's sequential estimate, never a part's, so splitting cannot pull a task under it.

## Rejected

- Stop rule in reply-lint (a no-dependency part with no builder spawn returns the turn): it would have returned 3 of 3 correct bench launches, where a no-dependency part was rightly held on an owner answer; it also passes with the defect present, since a session may declare `whole`; and it misses launches made after another Stop-hook return (26 of 478 turns in 14 days).
- Sibling check at the spawn (PreToolUse): a call's later siblings are not yet in the transcript when its hook runs (624 of 657 adjacent Agent call pairs in 14 days had the earlier call's result written before the later call).
- Tracking landed state or plan identity across sessions, or shared files from `files:` (read pointers, not edits): not mechanical.
- Separate `estimate:` and `part:` lines: one line carries both.

## Open measurements and known effects

- No cap on simultaneous builders. The repository's CI and the main session's context take several builders and review chains at once; neither bound was measured. The largest launch in 14 days before the change was 3 builders. Measure main-session context added per live part and concurrent CI runs per repository before adding a cap.
- `pace` keys tasks by worktree, so a split task enters it as several shorter tasks and lowers the tier medians that later estimates come from. Known effect, out of scope here; grouping by the `parts:` line is the candidate fix.
- Cost: each part pays its own spec, reviews and cross-check, for a lead time of the longest dependent chain instead of the sum. A chain over the threshold (every part after the previous one) pays a review chain per link with no parallel gain.
- `loop-cost-gate` keys long runs per repository and by program basename across worktrees, so three parallel builders that each run a long check once get the next rerun refused until a `# LOOP_COST:` line. A per-worktree key is a change to that gate, out of scope here.
