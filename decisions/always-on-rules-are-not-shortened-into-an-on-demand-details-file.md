# Always-on rules are not shortened into an on-demand details file

## Decision

The bullets of `rules/always-on.md` keep their full text in the always-loaded file. Moving a bullet's edge clauses, examples or negative cases into an on-demand companion file (`always-on.details.md`) to save context is rejected. A bullet is shortened only one at a time, with a replay comparison on its own case showing no drop, and the removed clause is deleted, not parked in a file nobody loads.

## Why

Measured on 2026-11-12. Arm A was the live file (4,330 words); arm B a short form of the four sections "Replies", "Authority and evidence", "Measurement" and "Scope" (2,426 words) with every full statement moved unchanged to `rules/always-on.details.md`, named in the header as the file to read when a rule's edge decides what to do. Fifteen target cases and two controls, fresh sessions, k=3 per arm, drops of two or more rerun to k=6, one model for all 120 runs, blind grader per case.

- No subject opened the details file: 0 of 60 arm-B runs. Text moved on demand stops acting; it is a deletion, not a layer.
- Five of fifteen target cases dropped by two or more passes; both controls held (gold control 3/3 in both arms; length control 3/3 with median reply length +0.9 %).

| Case | Bullet (words A -> B) | A | B | Clause the drop depends on |
|---|---|---|---|---|
| CASE-30 | Replies, existence witness (124 -> 54) | 3/6 | 0/6 | "read ... from its first line to its last; ... a search over it ... is not that read; ... never a reason to search elsewhere" |
| CASE-43 | Scope, durable record (107 -> 52) | 6/6 | 3/6 | the two parentheticals naming what is recorded (version or image, inputs, commands) |
| CASE-63 | Authority, duration (159 -> 75) | 5/6 | 3/6 | "a count of days from a delegate or from human pace is a claim" and the pace examples |
| CASE-5 | Authority, readings and owner's word (307 -> 150) | 3/3 | 0/3 | "an assignment, an end state or a wish named before that result is not that word ... a condition does not replace it" |
| CASE-57 | Measurement, ramp step (250 -> 102) | 3/3 | 0/3 | "neither the next step nor a rollback replaces that search, and a script's own stop rule does not decide it" |

In every drop the failing runs did exactly what the removed clause forbids. The clauses that look like elaboration (the negative case, the named substitute, the example of what does not count) are the part of the rule that changes behaviour.

Four bullets held at 3/3 in both arms: basis strength (CASE-66), bug alert (CASE-23), design note (CASE-24), check form (CASE-49B). Shortening only those would have saved 360 words of 4,330; the owner declined it as not worth the risk on rules every agent loads.

## Compared against

- Full short form with a details file: 44 % fewer words, five cases lost, details file never read. Rejected.
- Short form only for the four bullets that held: 8 % fewer words, evidence at k=3 with silver graders. Not taken.
- Leaving the file as is: taken.

## Limits of the evidence

CASE-5 and CASE-57 stand at k=3 only. Graders are silver except on CASE-4, CASE-5 and CASE-7. Arm A ran below its recorded baseline on six cases, three of which (CASE-74, CASE-22, CASE-26) could therefore not show a drop. Four target cases and one control were cut for the token ceiling and many sibling packets were not run. The details file was tested as a local copy next to the subject, on a frozen packet with no hooks. An earlier rewording pass recorded the same hole for the existence bullet (3/3 -> 0/3).

## Reopen only with

A replay comparison per bullet on that bullet's case, target not lower and controls held, or evidence that fresh sessions actually read an on-demand rules file at the moment the rule applies. The merge gate's `ALWAYS_ON:` field is where that evidence is stated.

Cost of the comparison, for planning a rerun: 120 subject runs, about 4.0 M new tokens (9.5 M counting cached prefix re-reads), about 16 minutes.
