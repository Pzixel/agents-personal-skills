# A lever starts from who consumes the work: no rule-text placement moves it

Decision (2026-11-06, retro-evals CASE-69). The rule «before a lever is listed for any work on a measured path, name who consumes the work's result from the consumer's own code or manifest; work with no consumer is removed and its removal is the lever; only for surviving work, read the mechanism that makes it slow» is not carried as text anywhere in production. The Scope bullet is removed from `rules/always-on.md` (dark text costs context in every session), and `orch/hooks/task-type-checklist.py` stays as it was before this case. The next lever class is a Stop-hook rule in `reply-lint` that returns a reply listing a speed-up lever for a step without a consumer read, the same class as the rules that closed the next-step and held-checks cases; it is to be backtested on history and measured before it lands.

Why. A session was asked for levers to bring a deploy down to 1-2 minutes. The agent listed «flatten the chain / a shorter re-check interval» for a serial chain of 8 schema Jobs (215 s, 30 s re-check per hop) and «webhook» for a pickup stage whose cause was «not found». The owner named the hypothesis the investigation should have produced (the services have no real dependencies); one read-only probe confirmed it in 2 minutes: 7 of 8 edges were consumed by nothing.

Measurements on the witness packet (fresh headless subjects unless stated):
- In-session arm, the Scope bullet quoted inside the packet next to the decision: 0/3 -> 3/3. The only placement that reached the floor (3/3 at k=3).
- Always-on Scope bullet (applied 6f05f77, removed here): 0/6 -> 1/6.
- Hook Optimization checklist with two reworded lines and a widened trigger (landed, then reverted here): 0/3. The text reached all three subjects' transcripts.
- Hook checklist with the Scope bullet verbatim as the checklist line: 1/3.
- Earlier packet injection of the existing checklist text: 0/3; its «minimum by contract» is filled from the declaring manifest and the README order.

Conclusion: a rule text in any production placement does not move this decision; only text placed at the decision itself did.

Trigger forms backtested (owner prompts over 28 days; kept as a record so no one retries them without new evidence):
- Wide form, duration targets in general plus the bare word for «lever»: 6756 prompts, +108 new fires, 6 real (~3.5 false per day). Rejected.
- Anchored form, the word for «lever» together with a number with a unit or a speed or resource word: 6780 prompts, 9 new fires, 5-6 real (two readings labelled two of the nine differently, precision 56-67 %). Landed and reverted: its only payload measured 0/3.
- Linear time matters for any future form: the unanchored version of the anchored form backtracked to seconds on prompts of 5-20 KB, beyond the hook's 5 s timeout.

Compared against, not run: widening the weak-spot bullet from «a number» to «a number or a lever»; the subjects in the 0/3 arms already held numbers back and still never asked who consumes the work.

Rows, packets and grades: retro-evals, cases/CASE-69.
