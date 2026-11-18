# A destructive plan asks the owner only for an effect with no way back

## Decision
A plan run as `bash <plan>  # DESTRUCTIVE_PLAN sha256=<hex>` that passes the form checks runs at once. It is logged as class `plan`, owner `not_needed`, and the agent reports its scope, count and undo right after the run. The owner approves it (permission prompt, or `APPROVE <sha256>` where the session cannot prompt) only when `gate_policy.plan_requirement` finds one of these:
1. a `# pin:` line (the owner reads the pinned file's facts);
2. a `# irreversible:` value other than `none` (case-insensitive, trimmed). The header is mandatory, exactly one line. `none` is allowed only when every effect can be returned to its prior state by a step the plan states;
3. the floor: the classifier's `impossible` reading of the plan text (a Secret write, a namespace/PVC/CRD delete, a force push, rm outside owned paths, SQL DROP/TRUNCATE/ALTER DROP and DELETE without WHERE where the reading sees them), a privileged hit (sudo and the like hide the command), a quote the reading cannot pair, a reading that runs out of work, or a non-comment line holding `TRUNCATE` or `DROP <object word>`. The declaration is about production and shared state; a local deletion outside owned paths does not make its value other than `none`: it is the floor that asks the owner for it. Bare commands keep their classes. A local DROP DATABASE still asks through the SQL words; proving a target local (files or a local container) is a separate change.

The marker form, main session only, size limits (`plan_max_lines` 41), pins and the bare-command classes are unchanged. An approval file is spent only by an owner-needed run. Plan log rows carry `plan_path`, `cwd`, `owner` (`not_needed`, `requested`, `approved`, `form_refused`), `reason` and `stale_approval`.

## Why
The owner approved every plan, read none of them, and asked for 0-2 approvals a day, all on production actions with no way back. Reversible and additive production writes are now the agent's to run and report.

## Measured
Measured on 2026-11-18 with `python3 orch/tools/backtest/replay.py plan-approvals --since 2026-10-26 --until 2026-11-19 --labels <labels.tsv>` over one owner's destructive log. The input was 76 allowed plan runs (class approval, decision allow). The stored plans have no `# irreversible:` header, so a label column stands in for it, assigned by reading each plan. The labels file covers 49 runs.

| outcome of the 76 runs | runs |
|---|---|
| owner: declared irreversible (label) | 13 |
| owner: floor alone (labelled none) | 9 |
| runs without the owner (labelled none) | 22 |
| labelled none, plan file gone or rewritten since the run | 5 |
| log row cut before the plan path (a leading `# OUTCOMES:` line) | 10 |
| unlabelled: floor raises | 1 |
| unlabelled: floor silent | 1 |
| unlabelled: plan file gone or rewritten | 15 |

Owner requests per day (declared plus floor alone, labelled runs): 11-08 2, 11-09 2, 11-12 3, 11-13 4, 11-16 1, 11-17 5, 11-18 5; declaration alone is above 2 on 11-17 (3) and 11-18 (4).

All 9 floor-alone requests are on plans the labels call reversible. 3 are a database DROP, which the SQL words take. 2 are Secret patches that only add keys, which the classifier cannot tell from a replace. 4 are local rm plans of one day, outside owned paths.

## Left to the declaration (known gaps)
The floor reads only what is plainly visible in the plan text. Writes it does not see rest on the agent's `# irreversible:` line:
- DELETE or UPDATE without WHERE beyond what the classifier reads;
- ClickHouse `ALTER ... DELETE` through an unbound client head;
- HTTP DELETE or a vendor call;
- a write inside a script run in a pod, or any write whose text is not in the plan;
- `ssh host -- <command>` and a function wrapping ssh, which neither the floor nor the bare classes read;
- a command whose head is an array expansion (`"${K[@]}" delete pvc data-0`), which the classifier does not read;
- `dropdb`, `redis-cli FLUSHALL`, `DROP OWNED BY`;
- the bare `approval` writes in a plan declared `none`: `helm uninstall`, `kubectl delete pv`, `kubectl delete all --all`, `rsync --delete`.

This is the accepted risk of the owner's ruling. The threat model is unchanged (`destructive-gate-threat-model-is-agent-mistakes.md`): the gate catches mistakes, and a false `none` is a mistake the floor catches only where it can see it.

## Rejected
- A local-target exemption by the absence of remote words: nine production plan bodies passed it.
- A local-file exception, built and removed in this change: the floor left local-effect hits (rm or `find -delete` outside owned paths, git discards outside a worktree) to the agent when no non-comment line held `ssh`, `kubectl` or `docker`. It cleared 4 runs in 24 days, then needed three rounds of bounding rules (functions shipped over ssh, variable-, wrapper- and array-held carriers). With them the floor alone asked 10 times against 5 before it: two reversible production plans and two local plans with a carrier word in a file name now asked. Without it: 9.
- An open word list for `# undo:`: it missed wordings and fired falsely.
- Per-statement SQL parsing and reading SQL files from the plan: it leaked and over-fired both ways.
- A raw regex over the whole body: `[^;]*` crossed lines.
- A setting that turns the owner back on for every plan: no installer asked for one, and the measured rate is the reason for the change.

## Resume signal
Reopen when the replay shows a plan declared `none` that destroyed something the owner wanted back. Local targets (files and local containers) are a separate change, reopened when floor requests on local work cost more than 1 a day over a week of the replay; it needs a target proof the plan text can carry, not a word match.
