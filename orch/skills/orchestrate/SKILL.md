---
name: orchestrate
description: Required before editing code or investigating a failure (why a test fails, a bug) in a repository, unless the edit is one line. The main thread states the task and runs this flow - `orch:builder` builds and runs the check, Fable reviewers review and cross-check, up to two fix rounds, report.
---

# Orchestrating a task

## Why

One Opus 5.5 session at effort `low` brought a large feature to the finished state for $5.43, against $14.59 at `medium` and $34.48 on Fable. Only Fable reviews reliably (known defects found: Fable 5/5, Opus 4/5, Sonnet 1/5), and one review missed 2 of the 4 shared blockers, so two reviews plus a cross-check; Opus `low` writes 3-4x fewer tests, so a fix round in the builder's own context is part of the flow. Evidence: `~/Documents/Repos/backend/tmp/orch-probe/efficiency/FINAL.md`.

## Proof or ship

Every task is declared `proof` or `ship` in the first line of the step 1 prompt, and the builder repeats it in SPEC.md.

- `proof`: the deliverable is a number or a scenario on the real system (prototype behind a switch, load harness, probe, one-off migration check). One builder, a time cap stated in the prompt (default 4 hours), no DESIGN.md, no steps 2-6; the builder's reply carries the measurement and its blind spot; the code is throwaway unless the user promotes it. A task with a numeric goal always starts as `proof`; `ship` begins only after the number exists.
- `ship`: the full flow below.

Review depth follows whether the code survives, not how large the change is. Evidence: retro 2026-10-12 — 157 builders and 95 reviewers on one throughput task, with full Fable rounds on a planner variant that never merged, and five builders in a row on it losing context.

## The flow

`<task>` is the user's text verbatim (or their path); `<ws>` the workspace (worktree and branch per the repository's rules). `<head>` is the last hash in the builder's `commits`, `<base>` the parent of its first (`<first>^`); after a fix round `<old head>` is the previous `<head>`. `<notes>` is the `orch-notes/` directory the builder names in its reply (inside the checkout it edits, git-excluded); review files go there too. Wait for each reply before the next step.

1. Builder. The brief carries what discovery would cost the builder: `files:` from one `orch:Explore` run, `size:` as the expected diff, and a `check:` that runs on the real path. A size over about 400 LOC or more than one subsystem is split into slices before spawning, each slice with its own `check:`; a hook refuses a builder prompt without `files:` and `check:` lines (retro 2026-10-12: 30+ builders ran out of context on discovery).
```
Agent(subagent_type: "orch:builder", description: <3-5 words>, prompt:
task: <task>
workspace: <ws>
files: <file:line pointers from the Explore run>
size: <expected diff in LOC and the subsystems touched>
check: <the run on the real path that proves the work; never only unit tests>
First create <repo>/orch-notes/ (the checkout you edit first; append `orch-notes/` to its .git/info/exclude) and write SPEC.md there: a `check:` line (the user's check if given), one `risk:` line per risk. With any risk, write DESIGN.md there and stop for a design review. Never commit orch-notes/; name it in the reply.
Checklist: real-database tests for SQL; component tests for every UI state; update callers of changed signatures and scripts; strict load of changed CI/config files; encoded-path variants for route guards.
Reply: commits, check, deviations, risks[], checklist, questions[].)
```
2. Design review, only when `risks[]` is non-empty; one reviewer, one round:
```
Agent(subagent_type: "orch:reviewer", model: "fable", description: ..., prompt:
mode: review
Review <notes>/DESIGN.md against the task. Findings file: <notes>/review-design.md.
task: <task>)
```
Then, without asking the user: 0 BLOCKER → SendMessage the builder `design reviewed, proceed to code` plus the majors as design fixes; a BLOCKER the spec settles → one design fix round with the builder, then the same message. Stop for the user only on a product question (customer-visible behaviour the spec does not settle) or a design BLOCKER needing an owner choice the spec leaves open (storage, external contract, data migration).
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
- `questions[]` from any agent: the main session answers technical ones by SendMessage (before step 3 only when the answer changes the code, otherwise into the report); product questions go to the user (the stop condition in step 2).
- At 220k context a hook stops the builder; it commits and writes a handoff file. Spawn a fresh builder: the step 1 prompt plus `handoff: <file>; the spec and design stand`, and continue from the step it stopped at.

## Investigations

A question without a code change: one `orch:prod-probe` (evidence from running systems, report path in the prompt, and an `access:` line with the route to every system it reads, from the `infra-access` skill; a hook refuses the spawn without it) or one `orch:Explore` (where code lives, answer in the reply). No lead agent. If a fix is needed, continue with step 1 in the same chat.

## Waiting

Run the condition command once in the foreground first: `wait-for` exits 2 when the command itself is broken (wrong flag, regex, path), so a wrong condition fails in seconds instead of timing out as "still pending". Then, in the main session, a wait over about two minutes goes to the harness `Monitor` tool (zero turns, wakes once on the terminal state); an `orch:watcher` only collects the evidence after it fires, or stands in when `Monitor` is unavailable. Short waits: `~/.claude/tools/wait-for --every 30 --max 570 -- <condition>` in the foreground; name terminal failure exits with `--fail-on`, e.g. `wait-for --fail-on 1 -- gh pr checks <n>` exits 3 at once on a failed check. Subagents never run anything in the background (retro 2026-10-12: 11 waits delegated to an LLM ended its turn or ran out of turns, 18 h).

## Destructive commands

No text in a command changes the class the hook assigns it. A production write is batched into one plan script with `# scope:`, `# undo:` and `# count:` header lines and run from the main session only as `bash <plan>  # DESTRUCTIVE_PLAN sha256=<hex>`, so the owner answers one prompt per batch. In a session that cannot prompt, give the owner the plan path and its hash; the owner reads the plan file, since no prompt shows the script, and replies `APPROVE <hash>`; that reply is the approval, one run per approval. For a command without an undo, the owner runs it or unlocks a credential for it. When the owner is away, do not run a plan: name it in the handoff and continue other work. Classes and thresholds: `destructive` in `tools/orchestrate-params.json`.

Supersedes the process used until 2026-09-30; history in `evals/REPORT-2026-09.md` and FINAL.md.
