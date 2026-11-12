# main-budget-gate reminds and never denies

## Decision

`orch/hooks/main-budget-gate.py` and `gate_policy.budget_decision` keep only the
reminder: a hands-on main-session call at context above 80k tokens, with 4 or more
hands-on calls already in the window (since the last owner message or agent spawn),
gets the reminder as additional context. The trigger values and the reminder's rule
live in `orchestrate-params.json` `main_budget` (`DEFAULT_PARAMS` equal); since
2026-11-12 the reminder states the read size rule (below) instead of "batch or
delegate". The count-based deny (16
calls) is removed, and with it the `MAIN_BUDGET_OVERRIDE` marker, its `override`
decision and the `~/.claude/main-budget-overrides.jsonl` writer: nothing is left to
override. A `MAIN_BUDGET_OVERRIDE:` line is now an ordinary comment line. Owner
decision 2026-11-12.

## Why

Measured from Claude Code transcripts, 304 interactive main sessions,
2026-10-12..11-11, list prices (retro-evals `runs/main-budget-gate/`, commit
de2dd30a: `extract.py`, `analyze.py`, `context_cost.py`, `carry.py`, `reconcile.py`,
`RESULT.txt`, `RESULT-context.txt`, `RESULT-carry.txt`, `RESULT-reconcile.txt`,
`denials-per-session.json`, `denials-with-next-call.txt`):

- Hands-on main-session calls above 80k context cost $1,989 in the month.
- By length of series (hands-on calls between two boundaries): series of 1-8 calls
  hold 78 % of that cost ($1,556), 9-16 calls 15 % ($293), longer than 16 calls 7 %
  ($141; 120 series, 3,060 calls). Sequential reads break even against one read
  agent at 9.6 / 7.8 / 6.7 / 5.6 hands-on reads by context band (0-80k / 80-150k /
  150-250k / 250k+), so a short series of small reads is not cheaper delegated; a
  series of large reads is (below).
- Break-even (private eval notes, run read-break-even; method, prices and variants in
  ORIGINS.md, Read break-even): 311 interactive main sessions since 2026-10-13, 16,247
  hands-on reads, 887 probe runs, list prices. One read agent costs about $0.53 with
  its reply's tail. By result size at 80-150k it breaks even at 11.9 reads under 1k
  tokens, 6.2 at 1-3k, 2.6 at 3-8k, 1.0 above 8k; size explains 60 % of the variance,
  the band 5 %. Reads batched into one request break even at 22k / 18k / 21k / 26k
  tokens by band. Rule (params `main_budget`): hand the reads still ahead to one
  `orch:scout` or `orch:prod-probe` when they are expected to return more than about
  20,000 tokens of results (about 45 KB) or to take more than about 7 separate
  requests (median count above 80k); otherwise read them here, batched. The reminder
  states the size arm, the orchestrate skill both. The 887 probes' work would have
  cost 3.2-4.6 times more in the main session. This replaces "about 6 main calls
  (probe 5.8, scout 6.6)", an average over all contexts and sizes. The trigger stays
  at 80k and 4 calls: a later one would let 3-8k-token reads pass their 2.6-read
  break-even unreminded. It counts every hands-on call, edits and builds included,
  while the text speaks of reads; the former "batch the rest" advice is gone. A
  production question under the thresholds is now read in the main session without
  the `access:` line a probe spawn needs.
- Replay of the deny rule on that history (a model, not the deployed hook) stops
  899 calls in 91 sessions, worth $70. By `reconcile.py` (`RESULT-reconcile.txt`):
  364 on 2026-10-24..25 when the orch plugin was not loaded, 242 not Bash without
  the marker (mostly tools outside the hook's matcher), 179 Bash without the marker
  (denied or let through), 114 with the marker. The hook log holds 119 overrides at
  that position.
- Against that: the override log holds 969 overrides in the month, 850 of them on
  calls the gate would not have denied (window below 16 or context at most 80k;
  recounted from `~/.claude/main-budget-overrides.jsonl` on 2026-11-12). Denials:
  178 tool results carrying the deny text in the main transcripts of 46 of the 51
  sessions that used the marker (`denials-per-session.json`); the other 5 sessions' transcripts (127 overrides,
  all 2026-10-12) are gone, and sessions that never used the marker were not counted.

The deny bought at most $70 a month and cost about a thousand marker lines and ~180
refusals of the owner's own sessions.

## Rejected alternatives

Each narrowing replayed on the same history, as a share of what the current rule
stops (RESULT.txt section E):

| Variant | Calls stopped, share of current rule | Sessions | Cost of stopped calls |
|---|---|---|---|
| leading `cd` not counted | 895, 99.6 % | 91 | $70 |
| main-only tools not counted | 801, 89.1 % | 86 | $62 |
| delivery commands and record files free | 803, 89.3 % | 82 | $63 |
| retro-evals sessions exempt | 688, 76.5 % | 65 | $48 |
| all four | 540, 60.1 % | 56 | $38 |
| current rule with limit 30 | 323, 35.9 % | 12 | $22 |
| all four, limit 30 | 204, 22.7 % | 5 | $15 |

Each keeps the deny, the marker and its noise for a few tens of dollars. Removing the
whole hook was not chosen: the reminder stays; its effect on batching is not measured.

## What can get worse, and how to recount

The cost that grows if sessions stop batching is the long series: series longer than
16 hands-on calls, 120 series, 3,060 calls, $141 of $1,989 per 31 days. The reminder
now also fires on calls that were denied or overridden before: an estimate, not a
bound, of about 1,180 more reminders per 31 days (899 replayed stops plus 281 logged
marker calls at window 4-15); 242 of the 899 are tools outside the hook's matcher and
get no reminder, and calls that followed a former deny are not counted. Recount from
the retro-evals root: `python3 -I runs/main-budget-gate/extract.py tmp/mbo/calls.jsonl
&& python3 runs/main-budget-gate/analyze.py tmp/mbo/calls.jsonl` (calls.jsonl is
about 26 MB and not committed; RESULT.txt section D, buckets 17 and above). Re-adding a deny needs a new measurement showing the long-series cost well
above this baseline. The reminder's text changed on the same day the deny was removed
(the trigger did not move), so a later change in long-series cost against this
baseline belongs to both changes jointly.
