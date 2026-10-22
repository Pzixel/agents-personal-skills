# Review A/B stay on Fable at effort high; cross-check on Opus is undecided (shadow run)

Decision (2026-10-22): the two independent reviewers of the orchestrate flow stay `model: "fable"` at the reviewer's default effort (high). Reviewer B is not replaced by Opus, and effort medium is not used. The cross-check step keeps Fable until the five-task shadow comparison in the orchestrate skill (step 4) is logged.

Why: replay on four reviewed branches of two private Rust services at their original heads (an analytics read-path PR, a collector backfill, a dispatch-path rewrite of about 90 files, a keyed-concurrency consumer). Reference R = the 16 BLOCKER/MAJOR rows present in the Fable cross-check tables. Recall on R at BLOCKER/MAJOR (counting a MINOR grade as found, in brackets): original Fable A 8/16 (10), original Fable B 10/16 (12), Opus independent review 7/16 (10), Fable at effort medium 7/16 (8), Opus cross-check of the same A/B files 14/16 (15). Cost per agent (list prices, 5-minute cache writes): Opus review $1.0-1.7 vs Fable high A $2.9-5.0 and B $2.0-3.5; Fable medium $1.2-2.9; Opus cross-check $0.5-1.5 vs Fable cross-check $1.1-2.3. Earlier nine-task measurement: B alone found 7 of 23 verified rows, 2 of 5 BLOCKERs (private eval notes, reviewer-b-contribution.md).

Compared against:
- Reviewer B on Opus: 22% of Fable spend, but Opus recall 7/16 is below both Fable reviewers; the two B-only BLOCKERs in the nine-task set would be at risk. Rejected.
- Fable at effort medium: output tokens are only $276 of $2.7K Fable spend, so the saving is in turns (about 55-65% of the high-A cost); recall 7/16 vs 8/16 and 1/4 on the largest branch. Rejected.
- Cross-check on Opus: 14/16 agreement; the two disagreements are the stale-revision-fence row of the dispatch-path rewrite (the current SPEC carries an owner override that the Fable table contradicts) and the explain-blockers row graded MINOR. Not a clean pass; decided by the shadow run (private eval notes, crosscheck-ab.tsv).

Blind spots: four tasks, 16 rows; the replay reviewers read the current SPEC files, which had moved for the dispatch-path rewrite; "new" BLOCKER/MAJOR rows raised by the replay arms (Opus cross-check 9, Opus review 11, Fable medium 7) were not verified and may be real misses of the original flow. Full tables: private eval notes, replay/COMPARISON.md.
