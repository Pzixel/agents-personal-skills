---
name: orchestrate
description: Required before editing code or investigating a failure (why a test fails, a bug) in a repository, unless the edit is one line. The main thread states the task and runs this flow - `orch:builder` builds and runs the check, reviewers (Fable, Opus for a small task) review and cross-check, up to two fix rounds, report.
---

# Orchestrating a task

## Proof or ship

Declare every task `proof` or `ship` in the first line of the step 1 prompt.

- `proof`: the deliverable is a number or a scenario on the real system. One builder, a time cap stated in the prompt (default 4 hours), no DESIGN.md, no steps 2-6; the code is throwaway unless the user promotes it, the run record is not: the version or image measured, the inputs, the commands or where the scripts live, and the numbers go into the body of the PR that lands the result, or, when nothing lands, appended to the one existing doc on that subject (never a new file per run); a script that will run again lives in a tracked tools directory, a one-off is named as scratch. A task with a numeric goal starts as `proof`.
- `ship`: the full flow below.

## The flow

`<task>` is the user's text verbatim; `<ws>` the workspace: a worktree under `<main-checkout>/.worktrees/<task>/` on a branch from the fresh remote default tip, created by the main session before step 1 and removed by it after merge or abandonment (rule: AGENTS.md git section). `<head>` is the last hash in the builder's `commits`, `<base>` the parent of its first (`<first>^`); after a fix round `<old head>` is the previous `<head>`. `<notes>` is the `orch-notes/` directory the builder names in its reply; review files go there too. Wait for each reply before the next step.

1. Builder. `files:` comes from one `orch:scout` run. The task's tier sets its reviews and their model (rule: decisions/reviewer-model-by-task-size.md):
- small: the change is no more than a value, a field or a config line, whatever file holds it; or every file in `files:` is presentation (templates, styles, copy) or configuration that holds no rule, threshold or route. Reviews on `model: "opus"`. A value, field or config-line change carries no `design: required` and has no step 2; a larger presentation change carries `design: required`.
- large: over about 400 LOC (`size:`) or more than one subsystem; split into slices, each with its own `check:`. Reviews on Fable.
- medium: anything else (a decision, a query, a handler, state, a config that holds a rule, a threshold or a route, tests, docs, scripts, build files, migrations, policy prose); carries `design: required`. Reviews on Fable.
With `design: required` the builder writes DESIGN.md first (scope and subsystems, size, the premises the fix rests on and how each was verified, the rejected alternatives) and stops. A hook refuses a builder prompt without `files:` and `check:` lines.
```
Agent(subagent_type: "orch:builder", description: <3-5 words>, prompt:
task: <task>
workspace: <ws>
files: <file:line pointers from the scout run>
size: <expected diff in LOC and the subsystems touched>
check: <the cheapest run that decides: logic as native tests; a third-party store through the one shared local container per store (a database per task, dropped at the end), never a container, image or volume per task; real behaviour on production after rollout as the last step; a container or image only to compare two adjacent variants, with its reason and its removal>
Reply: commits, check, size, deviations, risks[], checklist, questions[].)
```
2. Design review and simplify pass, when the prompt carried `design: required` or the step 1 reply carries a non-empty `risks[]` (a small task with risks is promoted to medium). Load the `simplify` skill in the main session and run its lens pass on the design: a design is capped at about 600 words (builder.md), so normally one lens agent applies all five lenses; at `plan_simplify.fan_out_min_words` words or more the fan-out of the plan rule applies (rule: superpowers-writing-plans-psilon/SKILL.md, decisions/plan-simplify-fan-out-by-plan-size.md). Shape by tier: small (also one sent back at 2a with `design: required`, which keeps its tier) runs the combined spawn below with `model: "opus"`; medium runs it on Fable; large runs the two Fable spawns further below, in one message on the same DESIGN.md revision, the reviewer checking the contract, not the lenses.
```
Agent(subagent_type: "orch:reviewer", model: "fable", description: ..., prompt:
/simplify lens pass (reuse, simplification, efficiency, altitude, premises) on <notes>/DESIGN.md (full path), on each decision and premise; also review it against the task: premises verified, callers listed, risks, size. Always write <notes>/DESIGN.simplify.md, an empty file when nothing is found, and <notes>/review-design.md for the contract findings.
task: <task>)
```
Large:
```
Agent(subagent_type: "orch:reviewer", model: "fable", description: ..., prompt:
mode: review
Review <notes>/DESIGN.md against the task: premises verified, callers listed, risks, size. Findings file: <notes>/review-design.md.
task: <task>)
Agent(subagent_type: "orch:reviewer", model: "fable", description: ..., prompt:
/simplify lens pass (reuse, simplification, efficiency, altitude, premises) on <notes>/DESIGN.md (full path), on each decision and premise. Always write <notes>/DESIGN.simplify.md, an empty file when nothing is found.
task: <task>)
```
Then, without asking the user: SendMessage the builder a message that starts with exactly `design reviewed, proceed to code` (the start the gate matches; any other message passes it) plus the majors of both files as design fixes; the builder applies them to DESIGN.md, the main session never edits it. After a BLOCKER design-fix round the step 2 spawns of the tier (the combined agent, or both) rerun on the new revision; at most 2 design-fix rounds. A BLOCKER that survives the second the main session rules on before the go-ahead: a technical one it decides and writes into the go-ahead message; one whose options differ on money, priority, or a client-visible effect goes to the owner (below). `plan-simplify-gate` refuses that go-ahead while `<notes>/DESIGN.simplify.md` is missing or its mtime is older than the last content change of DESIGN.md (max of its birth time and mtime); a lens agent counts only when its first message carries the DESIGN.md full path and the literal `/simplify`. Stop for the user only on a product question the spec does not settle or a BLOCKER whose options differ on money, priority, or a client-visible effect, or an irreversible effect on client data.
2a. Size, on the step 1 reply and on the redelivery after a 2a return: added plus removed lines of `git diff --stat <base>..<head>` (the builder's `size:` line) against the upper bound of the step 1 prompt's `size:` range. At 3x the estimate or more (set to 3x from the owner's "several times", not measured) do not go to step 3: SendMessage the builder back to the design (reshape from reuse; step 2 reruns on the revised DESIGN.md). One return only: when the second delivery is still at 3x or more and DESIGN.md states why the size is intrinsic, go on to step 3 and carry the reason into the step 7 report. A size deviation on a fix-round reply goes into the step 7 report. A ship task with no DESIGN.md sent back to the design gets `design: required` in the return message, so the builder writes DESIGN.md first and step 2 runs on it.
3. Two independent reviewers, in one message (`model: "opus"` for a small task):
```
Agent(subagent_type: "orch:reviewer", model: "fable", description: ..., prompt:
mode: review
Review <base>..<head> in <ws> against <notes>/SPEC.md and the task. Read the whole change, not a sample. Findings file: <notes>/review-a.md (second reviewer: review-b.md); do not read the other file.
task: <task>)
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
Re-check <old head>..<new head> in <ws> against <notes>/review-verified.md.
```
A new finding with file:line and how it was verified counts as verified and goes back to step 5.
7. Report to the user:
```
Result: <branch, head, check result>
Original scenario: <the observation on the real path: command or query, value, time; or "not verified">
Cost: <sum of the agents' reply usage, or "not shown">
Blockers/majors: <verified by cross-check> -> <open after recheck>
Unverified: <each path or claim no run has executed>
questions[]: <for the user only>
```

## Stop rules

- One chat per task. The main session reads only agent replies: no findings files, reports or diffs. Steps 1-7 with one fix round take 6 turns (8 with a design review and simplify pass, 10 with a size return, 2 more per design-fix round); only a second fix round, a question exchange or a re-spawn below adds turns.
- At most 2 fix rounds, and none without a new verified finding.
- Before step 5: when a BLOCKER is `check: missed`, instead re-spawn `orch:builder` with `effort: medium`: the step 1 prompt plus `handoff: <head>, <notes>/review-verified.md; the spec and design stand`. It does this and every later fix round.
- A reply whose check failed: one SendMessage `the check fails: <tail>; fix and rerun, same schema`; a second failure goes to step 7.
- `questions[]` from any agent: the main session answers technical ones by SendMessage (before step 3 only when the answer changes the code, otherwise into the report); product questions go to the user.
- When a hook stops a builder at 220k context with a handoff file, spawn a fresh builder: the step 1 prompt plus `handoff: <file>; the spec and design stand`, and continue from the step it stopped at; a handoff before step 2 has passed is spawned only after step 2 ran on the design, with both findings sets in its prompt (`plan-simplify-gate` refuses a builder spawn with `handoff:` while `<notes>/DESIGN.simplify.md` does not exist; a handoff after `proceed to code` passes on its existence alone, since the builder edits DESIGN.md while applying the findings).

## Investigations

A question without a code change: one `orch:prod-probe` (report path in the prompt and an `access:` line with the route to every system it reads; a hook refuses the spawn without it) or one `orch:scout`. If a fix is needed, continue with step 1.

## Waiting

Run the condition once in the foreground first (`wait-for` exits 2 on a broken condition). A wait over about two minutes in the main session goes to the harness `Monitor` tool; an `orch:watcher` collects the evidence after it fires, or stands in when `Monitor` is unavailable. Short waits: `~/.claude/tools/wait-for --every 30 --max 270 -- <condition>`; name terminal failure exits with `--fail-on` (`wait-for --fail-on 1 -- gh pr checks <n>` exits 3 at once on a failed check).

## Destructive commands

A production write the hook classes `approval` or `impossible` is batched into one plan script with the six header lines of the details "Destructive" section (`rules/always-on-claude.details.md`) and run from the main session only as `bash <plan>  # DESTRUCTIVE_PLAN sha256=<hex>`, after the owner's approval of the plan: the permission prompt, or `APPROVE <hash>` where the session cannot prompt (give the owner the plan path and its hash; one run per approval). A command without undo runs only from an approved plan that states what it does. An agent removes its own scratch (inside a worktree, `<repo>/tmp`, `/tmp`) with `~/.claude/tools/rm-own -r <path>`, which checks the real path; it never asks the owner to clean up. An agent stops its own leftover local processes (dev servers, debug binaries, port-forwards) with `~/.claude/tools/kill-own -f '<pattern>'` or `~/.claude/tools/kill-own <pid>` (`--list` finds them); it never asks the owner to kill them. Classes and thresholds: `destructive` in `tools/orchestrate-params.json`.
