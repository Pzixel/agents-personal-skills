# main-budget-gate reminds and never denies

## Decision

`orch/hooks/main-budget-gate.py` and `gate_policy.budget_decision` keep only the
reminder: a hands-on main-session call at context above 80k tokens, with 4 or more
hands-on calls already in the window (since the last owner message or agent spawn),
gets the batch-or-delegate reminder as additional context. The count-based deny (16
calls) is removed, and with it the `MAIN_BUDGET_OVERRIDE` marker, its `override`
decision and the `~/.claude/main-budget-overrides.jsonl` writer: nothing is left to
override. A `MAIN_BUDGET_OVERRIDE:` line is now an ordinary comment line. Owner
decision 2026-11-12.

## Why

Measured from Claude Code transcripts, 303 interactive main sessions,
2026-10-12..11-11, list prices (retro-evals `tmp/mbo/`: `extract.py`, `analyze.py`,
`context_cost.py`, `RESULT.txt`, `RESULT-context.txt`):

- Hands-on main-session calls above 80k context cost $1,989 in the month.
- By length of series (hands-on calls between two boundaries): series of 1-8 calls
  hold 78 % of that cost ($1,556), 9-16 calls 15 % ($293), longer than 16 calls 7 %
  ($141; 120 series, 3,060 calls). A read agent breaks even at about 6 main calls
  (`orch:prod-probe` 5.8, `orch:scout` 6.6), so the short series, where most of the
  money is, are not cheaper delegated.
- Replay of the deny rule on that history (a model, not the deployed hook: it
  counts denied attempts and overridden calls alike, tools outside the hook's
  matcher, and 364 calls from 2026-10-24..25 when the plugin was off) stops 899
  calls in 91 sessions, worth $70. The hook log holds 119 overrides at that position.
- Against that: the override log holds 969 overrides in the month, 850 of them on
  calls the gate would not have denied (window below 16 or context at most 80k;
  recounted from `~/.claude/main-budget-overrides.jsonl` on 2026-11-12). Denials:
  178 tool results carrying the deny text in the main transcripts of 46 of the 51
  sessions that used the marker; the other 5 sessions' transcripts (127 overrides,
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
whole hook lost too: the reminder is cheap and keeps nudging toward batching.

## What can get worse, and how to recount

The cost that grows if sessions stop batching is the long series: series longer than
16 hands-on calls, 120 series, 3,060 calls, $141 of $1,989 per 31 days. The reminder
now also fires on the calls that were denied or overridden before, up to about 1,180
more reminders per 31 days. Recount in retro-evals: `python3 tmp/mbo/extract.py`,
then `python3 tmp/mbo/analyze.py tmp/mbo/calls.jsonl` (section D, buckets 17 and
above). Re-adding a deny needs a new measurement showing the long-series cost well
above this baseline.
