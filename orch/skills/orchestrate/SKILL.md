---
name: orchestrate
description: Required before editing code or investigating a failure (why a test fails, a bug) in a repository, unless the edit is one line. The main thread states the task and runs this flow - `orch:builder` builds and runs the check, reviewers (Fable, Opus for a small task) review and cross-check, up to two fix rounds, report.
---

# Orchestrating a task

## Proof or ship

Declare every task `proof` or `ship` in the first line of the step 1 prompt.

- `proof`: the deliverable is a number or a scenario on the real system. One builder (its prompt carries `parts: <N> min; whole; mine: whole`), a time cap stated in the prompt (default 4 hours), no DESIGN.md, no steps 2-6; the code is throwaway unless the user promotes it, the run record is not: the version or image measured, the inputs, the commands or where the scripts live, and the numbers go into the body of the PR that lands the result, or, when nothing lands, appended to the one existing doc on that subject (never a new file per run); a script that will run again lives in a tracked tools directory, a one-off is named as scratch. A task with a numeric goal starts as `proof`. A `proof` of a fix is the function on a real artifact under old and new code, natively; a run on the real system is the rare exception, proven in the prompt by the fact no pure function on stored data answers and the cost of the refactor that would; it is one run read where the user reads it, and the `check:` line never carries a precondition as a stop rule («if the counter stays 0, stop»): a failed precondition is a result the builder answers with the next input.
- `ship`: the full flow below.
- A number that leaves the session (a docs entry, a panel, a decision, a sentence for a client) is a `proof` deliverable: before it is written, one `orch:prod-probe` briefed to break it (every way the number can be wrong, each decided by a read of our own data) runs against it; the main thread's own query is the hypothesis, the probe's report is the result.

## Parts

Before the step 1 spawn, write the task as parts: a part is something that can be built, reviewed and merged on its own. For each part name the parts it cannot be built without; a part that only uses another part's interface or data format does not depend on it, the format is fixed in both prompts instead. Two parts that edit the same file depend on each other: the later one is written `after` the other. Estimate the whole task in minutes as if the parts were built one after another (`~/.claude/tools/pace <repository>`; the tier's usual run when it has no landings). When that estimate is over `parallel.split_above_minutes` (orchestrate-params.json), every part with no dependency gets its own worktree, branch and `orch:builder`, all spawned in one message; a part that depends on others starts when they have landed, from the fresh default tip; steps 2-8 run per part. A part held on an answer from the owner is named as held in the reply, and the others start. Every builder prompt carries the line `parts: <N> min; <name>[ after <name>, ...]; ...; mine: <name>[, <name>]`: N, every part with what it comes after, and the part this builder owns (several names only at or under the threshold, where the task goes to one builder as before); names hold letters, digits, `.`, `_` and `-`. A task that is one part writes `parts: <N> min; whole; mine: whole`; over the threshold a single part is right only when no piece of it can be merged on its own. A hook refuses a builder prompt without a parsable line, or one that owns more than one part over the threshold; a prompt with any line starting `handoff:` (a re-spawn) is exempt.

## The flow

The steps below run once per part, several parts side by side; `<ws>`, `<notes>`, `<base>`, `<head>`, the tier, `size:` and `files:` are the part's own, `<task>` is shared. `<task>` is the user's text verbatim; `<ws>` the workspace: a worktree under `<main-checkout>/.worktrees/<task>-<part>/` (a one-part task keeps `<task>`), one per part, on a branch from the fresh remote default tip, created by the main session before step 1 and removed by it after merge or abandonment (rule: AGENTS.md git section). `<head>` is the last hash in the builder's `commits`, `<base>` the parent of its first (`<first>^`); after a fix round `<old head>` is the previous `<head>`. `<notes>` is the `orch-notes/` directory the builder names in its reply; review files go there too. Within a part, wait for each reply before its next step; the replies of other parts are handled as they arrive.

1. Builder. `files:` comes from one `orch:scout` run. The part's tier sets its reviews and their model (rule: decisions/reviewer-model-by-task-size.md):
- small: the change is no more than a value, a field or a config line, whatever file holds it; or every file in `files:` is presentation (templates, styles, copy) or configuration that holds no rule, threshold or route. Reviews on `model: "opus"`. A value, field or config-line change carries no `design: required` and has no step 2; a larger presentation change carries `design: required`.
- large: over about 400 LOC (`size:`) or more than one subsystem; written as parts (Parts), each with its own `check:`. Reviews on Fable.
- medium: anything else (a decision, a query, a handler, state, a config that holds a rule, a threshold or a route, tests, docs, scripts, build files, migrations, policy prose); carries `design: required`. Reviews on Fable.
With `design: required` the builder writes DESIGN.md first (scope and subsystems, size, the premises the fix rests on and how each was verified, the rejected alternatives) and stops. A hook refuses a builder prompt without `files:`, `check:` and `parts:` lines. The prompt, and every SendMessage after it, carries no landing instruction (PR, push, merge): landing is step 8.
```
Agent(subagent_type: "orch:builder", description: <3-5 words>, prompt:
task: <task>
workspace: <ws>
files: <file:line pointers from the scout run>
size: <expected diff in LOC and the subsystems touched>
parts: <the task's parts line (Parts), the same on every part's prompt except `mine:`>
check: <the cheapest run that decides: logic as native tests; a third-party store through the one shared local container per store (a database per part, dropped at the end), never a container, image or volume per task; real behaviour on production after rollout as the last step; a container or image only to compare two adjacent variants, with its reason and its removal>
Reply: commits, check, size, deviations, risks[], checklist, questions[].)
```
2. Design review and simplify pass, when the prompt carried `design: required` or the step 1 reply carries a non-empty `risks[]` (a small task with risks is promoted to medium). Load the `simplify` skill in the main session and run its lens pass on the design: a design is capped at about 600 words (builder.md), so normally one lens agent applies all five lenses; at `plan_simplify.fan_out_min_words` words or more the fan-out of the plan rule applies (rule: superpowers-writing-plans-psilon/SKILL.md, decisions/plan-simplify-fan-out-by-plan-size.md). Shape by tier: small (also one sent back at 2a with `design: required`, which keeps its tier) runs the combined spawn below with `model: "opus"`; medium runs it on Fable; large runs the two Fable spawns further below, in one message on the same DESIGN.md revision, the reviewer checking the contract, not the lenses.
```
Agent(subagent_type: "orch:reviewer", model: "fable", description: ..., prompt:
/simplify lens pass (reuse, simplification, efficiency, altitude, premises) on <notes>/DESIGN.md (full path), on each decision and premise; also review it against the task: premises verified, callers listed, risks, size; for a shared mechanism, "suppose it is built: what new bottleneck appears, where does it break for every consumer, who gets worse". Always write <notes>/DESIGN.simplify.md, an empty file when nothing is found, and <notes>/review-design.md for the contract findings.
task: <task>
parts: <the part's `parts:` line; review only the part `mine:` names>)
```
Large:
```
Agent(subagent_type: "orch:reviewer", model: "fable", description: ..., prompt:
mode: review
Review <notes>/DESIGN.md against the task: premises verified, callers listed, risks, size; for a shared mechanism, "suppose it is built: what new bottleneck appears, where does it break for every consumer, who gets worse". Findings file: <notes>/review-design.md.
task: <task>
parts: <the part's `parts:` line; review only the part `mine:` names>)
Agent(subagent_type: "orch:reviewer", model: "fable", description: ..., prompt:
/simplify lens pass (reuse, simplification, efficiency, altitude, premises) on <notes>/DESIGN.md (full path), on each decision and premise. Always write <notes>/DESIGN.simplify.md, an empty file when nothing is found.
task: <task>
parts: <the part's `parts:` line; review only the part `mine:` names>)
```
Then, without asking the user: SendMessage the builder a message that starts with exactly `design reviewed, proceed to code` (the start the gate matches; any other message passes it) plus the majors of both files as design fixes; the builder applies them to DESIGN.md, the main session never edits it. After a BLOCKER design-fix round the step 2 spawns of the tier (the combined agent, or both) rerun on the new revision; at most 2 design-fix rounds. A BLOCKER that survives the second the main session rules on before the go-ahead: a technical one it decides and writes into the go-ahead message; one whose options differ on money, priority, or a client-visible effect goes to the owner (below). `plan-simplify-gate` refuses that go-ahead while `<notes>/DESIGN.simplify.md` is missing or its mtime is older than the last content change of DESIGN.md (max of its birth time and mtime); a lens agent counts only when its first message carries the DESIGN.md full path and the literal `/simplify`. Stop for the user only on a product question the spec does not settle or a BLOCKER whose options differ on money, priority, or a client-visible effect, or an irreversible effect on client data.
2a. Size, on the step 1 reply and on the redelivery after a 2a return: added plus removed lines of `git diff --stat <base>..<head>` (the builder's `size:` line) against the upper bound of the step 1 prompt's `size:` range. At 3x the estimate or more (set to 3x from the owner's "several times", not measured) do not go to step 3: SendMessage the builder back to the design (reshape from reuse; step 2 reruns on the revised DESIGN.md). One return only: when the second delivery is still at 3x or more and DESIGN.md states why the size is intrinsic, go on to step 3 and carry the reason into the step 7 report. A size deviation on a fix-round reply goes into the step 7 report. A ship task with no DESIGN.md sent back to the design gets `design: required` in the return message, so the builder writes DESIGN.md first and step 2 runs on it.
3. Two independent reviewers, in one message (`model: "opus"` for a small task):
```
Agent(subagent_type: "orch:reviewer", model: "fable", description: ..., prompt:
mode: review
Review <base>..<head> in <ws> against <notes>/SPEC.md and the task. Read the whole change, not a sample. Findings file: <notes>/review-a.md (second reviewer: review-b.md); do not read the other file.
task: <task>
parts: <the part's `parts:` line; review only the part `mine:` names>)
```
4. Cross-check (`model: "opus"` for a small task):
```
Agent(subagent_type: "orch:reviewer", model: "fable", description: ..., prompt:
mode: crosscheck
Inputs: <notes>/review-a.md, review-b.md (a missing file means no findings), <base>..<head> in <ws>, <notes>/SPEC.md. Write <notes>/review-verified.md. Mark each BLOCKER `check: caught|missed` (missed = the declared check passes with the defect present).)
```
5. Fix round, only when the verified table holds a BLOCKER or MAJOR; otherwise go to step 7. SendMessage to the same builder:
```
Fix round <n>. Verified findings: <the table from the cross-check reply>. Fix each, rerun the check, reply in the same schema.
```
6. Recheck. SendMessage to the step 3 reviewer of review-a.md:
```
mode: recheck
Re-check <old head>..<new head> in <ws> against <notes>/review-verified.md. Each row: `fixed` / `not fixed` / `not checked`, with the read at <new head> that decides it (file:line as read, or command and output line); a row with no read at the new head is `not checked`.
```
A new finding with file:line and how it was verified counts as verified and goes back to step 5.
7. Report to the user, one report per task, written after the last part that can land now has landed (or stopped), with a `Result` line per landed part and each held or not yet started part named with what it waits for:
A builder's statement about production's current state labelled `claimed` (or carrying no read) is relayed in this report only as a hypothesis with the read that decides it.

```
Result: <branch, head, check result>
Original scenario: <the observation on the real path: command or query, value, time; by default the pure function the result is computed by, run read-only on today's production rows, now; a scheduled event is awaited only as the rare exception, with the WAIT_SHOWS proof (the fact no pure function on stored data answers, and why the refactor that would is impossible or beyond the budget); or the scenario stated as not run, with what blocks it>
Cost: <sum of the agents' reply usage, or "not shown">
Blockers/majors: <verified by cross-check> -> <open after recheck>
Unverified: <each path or claim no run has executed>
Rollout: <after a rollout, every pod not Running on the new image, each with its log tail and its last success before the rollout (e.g. a CronJob's lastSuccessfulTime), read by this session or its watcher; a pod is "not ours" only on those two reads, never on a delegate's or PR body's "fails today"; or "not read">
questions[]: <for the user only>
```
8. Land, per part, `ship` and `proof` alike, run by the main session only (merge-gate refuses a landing from any delegate): push the branch; open a PR to the default branch whose body carries the decisions and rejected alternatives from SPEC/DESIGN (or the proof run record) and the check output; wait for CI per Waiting (`wait-for --fail-on 1 -- gh pr checks <n>` or a Monitor) until green; merge with the merge-gate fields `CHECK_RAN`, `PROD_POSITION`, `WAY_BACK`, `WORSE_FOR`, plus `ALWAYS_ON` when an always-on rules file changed; then remove the worktree and delete the branch locally and remotely (rule: AGENTS.md git section). A part whose default tip moved since its branch was cut (a sibling landed) is first rebased onto the fresh tip and its `check:` rerun on that revision before the push or merge; its reviews stand when the rebase had no conflict, and step 3 reruns on the rebased range when it had one. Then every part whose dependencies have now all landed starts at step 1, all of them in one message. A repository without a PR flow (agents-personal-skills) gets a fast-forward push to master after the same reviews, with the same fields.

## Stop rules

- One chat per part. The main session reads agent replies first; it opens a findings file, report or diff when a reply is ambiguous or contradicts its check, and hands one expected above the thresholds of Investigations to an `orch:scout` (code) or `orch:prod-probe` (production evidence) instead. Steps 1-7 with one fix round take 6 turns (8 with a design review and simplify pass, 10 with a size return, 2 more per design-fix round); only a second fix round, a question exchange or a re-spawn below adds turns.
- At most 2 fix rounds, and none without a new verified finding.
- An agent reply `WORKER RULES MISSING` (its preloaded worker rules did not arrive whole) is not retried: report it to the user with the agent type, and decide its cause by one read: run the skill's assembler command by hand in the agent's working directory, `/usr/bin/python3 <orch>/skills/worker-rules/assemble.py N` for N from 1 to 4, and read its closing lines (a missing or extra `part N of M end`, an `assembly failed` line, or text that is whole here, which points at delivery by the client).
- Before step 5: when a BLOCKER is `check: missed`, instead re-spawn `orch:builder`: the step 1 prompt plus `handoff: <head>, <notes>/review-verified.md; the spec and design stand`. It does this and every later fix round.
- A reply whose check failed: one SendMessage `the check fails: <tail>; fix and rerun, same schema`; a second failure goes to step 7.
- `questions[]` from any agent: the main session answers technical ones by SendMessage (before step 3 only when the answer changes the code, otherwise into the report); product questions go to the user.
- When a hook stops a builder at 220k context with a handoff file, spawn a fresh builder: the step 1 prompt plus `handoff: <file>; the spec and design stand`, and continue from the step it stopped at; a handoff before step 2 has passed is spawned only after step 2 ran on the design, with both findings sets in its prompt (`plan-simplify-gate` refuses a builder spawn with `handoff:` while `<notes>/DESIGN.simplify.md` does not exist; a handoff after `proceed to code` passes on its existence alone, since the builder edits DESIGN.md while applying the findings).

## Investigations

A question without a code change is read in the main session, batched into one message, unless the reads it needs are expected to return more than `main_budget.delegate_above_result_tokens` tokens of results (20,000, about 45 KB of output) or to take more than `main_budget.delegate_above_requests` separate requests (7), both in `orchestrate-params.json` with the measurement in its `_doc`; then it goes to one `orch:prod-probe` (report path in the prompt and an `access:` line with the route to every system it reads; a hook refuses the spawn without it) or one `orch:scout`. A number that leaves the session still goes through the probe of "Proof or ship", which takes precedence over these thresholds. If a fix is needed, continue with step 1.

## Waiting

Run the condition once in the foreground first (`wait-for` exits 2 on a broken condition). A check is a pure function on real input, run now; a wait of an hour or more is the rare exception and carries a `WAIT_SHOWS:` line with its proof (the fact only the event produces, why one pure function on stored data cannot answer it, why the refactor that would is impossible or beyond the budget); without that proof the function runs on today's rows now instead. A wait over about two minutes in the main session goes to the harness `Monitor` tool; an `orch:watcher` collects the evidence after it fires, or stands in when `Monitor` is unavailable. Short waits: `~/.claude/tools/wait-for --every 30 --max 270 -- <condition>`; name terminal failure exits with `--fail-on` (`wait-for --fail-on 1 -- gh pr checks <n>` exits 3 at once on a failed check).

## Destructive commands

A production write the hook classes `approval` or `impossible` is batched into one plan script with the header lines of the details "Destructive" section (`rules/always-on-claude.details.md`) and run from the main session only as `bash <plan>  # DESTRUCTIVE_PLAN sha256=<hex>`. `# irreversible:` is exactly `none` unless an effect with no way back touches something in use (a client sees it, an object or money at a vendor, a Secret value, data code or people read); a way back is a step the plan states that returns the prior state (a saved dump the plan takes and names is one); deleting data or an object nothing reads states on `# undo:` or `# effect_read:` the read that showed it. The plan runs at once unless the hook says it needs the owner (a declared in-use effect, or a no-undo effect the hook sees in the plan or a pinned file); run it first, and report a plan that ran without the owner right after, with scope, count and undo. Only for a plan the hook sends to the owner: the permission prompt, or `APPROVE <hash>` where the session cannot prompt (give the owner the plan path and its hash; one run per approval). A command without undo of its own runs only from a plan, which states its way back or the read that showed nothing reads the object, else, when the effect touches something in use, names it in `# irreversible:`. An agent removes its own scratch (inside a worktree, `<repo>/tmp`, `/tmp`) with `~/.claude/tools/rm-own -r <path>`, which checks the real path; it never asks the owner to clean up. An agent stops its own leftover local processes (dev servers, debug binaries, port-forwards) with `~/.claude/tools/kill-own -f '<pattern>'` or `~/.claude/tools/kill-own <pid>` (`--list` finds them); it never asks the owner to kill them. Classes and thresholds: `destructive` in `tools/orchestrate-params.json`.
