---
name: orchestrate
description: Required before editing code or investigating a failure (why a test fails, a bug) in a repository, unless the edit is one line. The main thread states the task and runs this flow - `orch:builder` builds and runs the check, Fable reviewers review and cross-check, up to two fix rounds, report.
---

# Orchestrating a task

## Proof or ship

Declare every task `proof` or `ship` in the first line of the step 1 prompt.

- `proof`: the deliverable is a number or a scenario on the real system. One builder, a time cap stated in the prompt (default 4 hours), no DESIGN.md, no steps 2-6; the code is throwaway unless the user promotes it. A task with a numeric goal starts as `proof`.
- `ship`: the full flow below.

## The flow

`<task>` is the user's text verbatim; `<ws>` the workspace: a worktree under `<main-checkout>/.worktrees/<task>/` on a branch from the fresh remote default tip, created by the main session before step 1 and removed by it after merge or abandonment (rule: AGENTS.md git section). `<head>` is the last hash in the builder's `commits`, `<base>` the parent of its first (`<first>^`); after a fix round `<old head>` is the previous `<head>`. `<notes>` is the `orch-notes/` directory the builder names in its reply; review files go there too. Wait for each reply before the next step.

1. Builder. `files:` comes from one `orch:Explore` run. A change that is more than a value, a field or a config line carries `design: required` in its prompt: the builder writes DESIGN.md first (scope and subsystems, size, the premises the fix rests on and how each was verified, the rejected alternatives) and stops; step 2 runs on it whatever `risks[]` says. A size over about 400 LOC or more than one subsystem is split into slices, each with its own `check:`; a hook refuses a builder prompt without `files:` and `check:` lines.
```
Agent(subagent_type: "orch:builder", description: <3-5 words>, prompt:
task: <task>
workspace: <ws>
files: <file:line pointers from the Explore run>
size: <expected diff in LOC and the subsystems touched>
check: <the run on the real path that proves the work; never only unit tests>
Reply: commits, check, deviations, risks[], checklist, questions[].)
```
2. Design review, when `risks[]` is non-empty or the prompt carried `design: required`; one reviewer, one round:
```
Agent(subagent_type: "orch:reviewer", model: "fable", description: ..., prompt:
mode: review
Review <notes>/DESIGN.md against the task. Findings file: <notes>/review-design.md.
task: <task>)
```
Then, without asking the user: SendMessage the builder `design reviewed, proceed to code` plus the majors as design fixes, after one design fix round if a BLOCKER exists. Stop for the user only on a product question the spec does not settle or a BLOCKER whose options differ on money, priority, or a client-visible effect, or an irreversible effect on client data.
3. Two independent reviewers, in one message:
```
Agent(subagent_type: "orch:reviewer", model: "fable", description: ..., prompt:
mode: review
Review <base>..<head> in <ws> against <notes>/SPEC.md and the task. Read the whole change, not a sample. Findings file: <notes>/review-a.md (second reviewer: review-b.md); do not read the other file.
task: <task>)
```
4. Cross-check:
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

- One chat per task. The main session reads only agent replies: no findings files, reports or diffs. Steps 1-7 with one fix round take 6 turns (8 with a design review); only a second fix round, a question exchange or a re-spawn below adds turns.
- At most 2 fix rounds, and none without a new verified finding.
- Before step 5: when a BLOCKER is `check: missed`, instead re-spawn `orch:builder` with `effort: medium`: the step 1 prompt plus `handoff: <head>, <notes>/review-verified.md; the spec and design stand`. It does this and every later fix round.
- A reply whose check failed: one SendMessage `the check fails: <tail>; fix and rerun, same schema`; a second failure goes to step 7.
- `questions[]` from any agent: the main session answers technical ones by SendMessage (before step 3 only when the answer changes the code, otherwise into the report); product questions go to the user.
- When a hook stops a builder at 220k context with a handoff file, spawn a fresh builder: the step 1 prompt plus `handoff: <file>; the spec and design stand`, and continue from the step it stopped at.

## Investigations

A question without a code change: one `orch:prod-probe` (report path in the prompt and an `access:` line with the route to every system it reads; a hook refuses the spawn without it) or one `orch:Explore`. If a fix is needed, continue with step 1.

## Waiting

Run the condition once in the foreground first (`wait-for` exits 2 on a broken condition). A wait over about two minutes in the main session goes to the harness `Monitor` tool; an `orch:watcher` collects the evidence after it fires, or stands in when `Monitor` is unavailable. Short waits: `~/.claude/tools/wait-for --every 30 --max 570 -- <condition>`; name terminal failure exits with `--fail-on` (`wait-for --fail-on 1 -- gh pr checks <n>` exits 3 at once on a failed check).

## Destructive commands

A production write the hook classes `approval` or `impossible` is batched into one plan script with the six header lines of the details "Destructive" section (`rules/always-on-claude.details.md`) and run from the main session only as `bash <plan>  # DESTRUCTIVE_PLAN sha256=<hex>`, after the owner's approval of the plan: the permission prompt, or `APPROVE <hash>` where the session cannot prompt (give the owner the plan path and its hash; one run per approval). A command without undo runs only from an approved plan that states what it does. An agent removes its own scratch (inside a worktree, `<repo>/tmp`, `/tmp`) with `~/.claude/tools/rm-own -r <path>`, which checks the real path; it never asks the owner to clean up. An agent stops its own leftover kubectl port-forwards with `~/.claude/tools/kill-forward <pid>` (`--list` finds them). Classes and thresholds: `destructive` in `tools/orchestrate-params.json`.
