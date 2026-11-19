# A destructive plan asks the owner only for an in-use effect with no way back

## Decision
A plan run as `bash <plan>  # DESTRUCTIVE_PLAN sha256=<hex>` that passes the form checks runs at once (logged class `plan`, owner `not_needed`; the agent reports scope, count and undo right after). The owner approves it (permission prompt, or `APPROVE <sha256>` where the session cannot prompt) only when `gate_policy.plan_requirement` finds:
1. a `# irreversible:` value (mandatory, one line) other than exactly `none` (case-insensitive, trimmed). The rule (`_IRREVERSIBLE_RULE`, shown by every refusal and ask): `none` unless an effect with no way back touches something in use (something a client sees, an object or money at a vendor, a Secret value, data that code or people read). A way back is a step the plan states that returns the prior state; a saved dump the plan takes and names is one. Deleting data or an object nothing reads is `none` only when `# undo:` or `# effect_read:` states the read that showed it (revision and what was searched); the hook does not read that evidence. A value opening with `none` and going on is a form refusal naming the test (logged as `declared`); an ask raised by the value shows the bare-`none` exit. `replay.py plan-approvals` lists each such refusal or request followed by a run of the same path without the owner;
2. the floor, over the plan text and each pinned file's hashed text: an `impossible` hit other than a SQL DROP/TRUNCATE/ALTER DROP or DELETE without WHERE (`_FLOOR_LEAVES`), a privileged hit, an unpaired quote, a reading out of work. What stays is in use by nature or unreadable: a Secret write, a namespace/PVC/CRD delete (cascades the text cannot list), a force push, rm, `find -delete` and git discards outside owned paths, a body not readable whole.

A pin line alone no longer asks; pinned-run facts go into the allow context and the `pinned_runs` log field, and a floor reason names the pinned file by path. Bare commands keep their classes: a bare DROP is refused and sent to a plan. Marker, size and pin forms are unchanged.

## Why
The owner approved plans unread and wants 0-2 a day that he reads. His ruling (2026-11-19, "only what is in use"): APPROVE stays for an effect with no way back on something in use; dead-data deletions with evidence, DROP and TRUNCATE with a saved dump or on dead objects, and pinned plans declaring `none` run without him. Accepted price: an agent's mistake about "nothing reads it" or a table name loses data unseen.

## Measured
In the 15 hours after the first narrowing (from 2026-11-18T14:41, read 2026-11-19), the owner was asked for 10 plans, from two sources: 6 approved runs in the hook's log, and 4 asks an agent made ahead of the hook (its transcript: 0 plan runs and 0 hook refusals for those plans, the ask repeated in 110 messages over 4 hours); 1 plan ran without him. By kind: 4 dead-data deletions, 2 forced by the pin line while declaring `none`, 2 runs of one DROP-with-dump plan (floor's SQL word match), 2 in-use effects (client notifications and send stops; account deletion at a vendor). Limit: an ask made ahead of the hook leaves no log row; the log and the replay do not see it.

`python3 orch/tools/backtest/replay.py plan-files <9 files> --labels <stand-in>` (read-only, latest content, pinned files from disk), on the six logged plan paths and three dead-data clears:

| decision on the nine files | own header value | stand-in (in use: the two above and one pinned pilot; every other file `none`) |
|---|---|---|
| owner asks | 5 | 3 |
| form refusal (`none` plus text) | 2 | 0 |
| runs without the owner | 2 | 6 |

The pinned pilot is in use: its header names an effect that stays after its undo, on data code reads.

The own-value column is higher because the files were written under the previous meaning (the clears name their dead-key deletion). The code alone removes the floor's SQL asks (1 plan, 2 runs) and pin-alone asks (2 plans); the pinned files' floor raised nothing.

Before this change, on 76 runs of 2026-10-26..11-19, the floor alone asked 9 times on plans labelled reversible: 3 DROPs (gone now), 2 Secret key adds (kept: an add reads like a replace), 4 local rm plans outside owned paths (kept).

## Left to the declaration (known gaps)
The floor reads only what is plainly visible in the plan or a pinned file; the rest rests on `# irreversible:`: SQL DROP, TRUNCATE and full DELETE (by this decision); writes the classifier misses (vendor and HTTP calls, scripts run in a pod, ssh-carried commands, array-expanded heads, runner functions and `trap` bodies, `dropdb`, `FLUSHALL`); files a pinned file sources or runs (read no deeper than its own text); the bare `approval` writes (`helm uninstall`, `kubectl delete pv`, `rsync --delete`). The kept floor over-fires on Secret key adds and on force pushes of unread branches. Threat model: `destructive-gate-threat-model-is-agent-mistakes.md`.

## Rejected
- Accepting a `none` prefix: a hedged `none on our data; labels stay after undo` passes an in-use effect unseen.
- Anything not exactly `none` asks (previous rule): 3 extra asks on the window's plans, above the target.
- An `# evidence:` header: the dead-data plans already state it on `# undo:`.
- Pinned files unread by the floor: reading them costs 15 lines, 1-34 ms a file.
- Earlier rounds, retry only on new evidence: a local-target exemption by absent remote words (nine production bodies passed it); a local-file exception (asked 10 against 9); an `# undo:` word list; per-statement SQL parsing; an owner-for-every-plan setting.

## Resume signal
Reopen when the replay shows a plan declared `none` that destroyed something in use, or the audit shows an ask rewritten to `none` over an in-use effect. Local targets reopen when floor asks on local work pass 1 a day over a replayed week.
