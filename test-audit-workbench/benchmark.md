# test-audit benchmark, 2026-10-14

## Provenance

The 20 cases are synthetic, not drawn from real sessions. The stub's author
generated the 20 task texts and truth rubrics from the pre-existing
`cases.json` and `rubric.json` in this directory, which predate the stub. The
stub's sentence "the audit is complete when its findings are reported" restates
the T10 Accept item of that rubric, so T10 is not independent of the rubric.

## Arms

Word counts are body words, frontmatter excluded.

| Arm | What the agent received | Body words |
| --- | --- | --- |
| NONE | no skill text | 0 |
| OLD(pre) | body before PR #16 (pre-dedupe) | 1763 |
| OLD | body at a0d0266, the body this change replaces | 1385 |
| LENS | four-lens rewrite, dropped | 598 |
| STUB v1 | first stub | 144 |
| STUB v2 | measured stub | 195 |
| SHIPPED | STUB v2 plus two Report scope clarifications; confirmation run on 4 cases | 209 |

## Results

Blind grading, one fresh agent per run. PASS means every Accept item met and no
Reject item hit.

| Iteration and cases | Arm | Cases passed (runs on T10 passed) |
| --- | --- | --- |
| 1: T01 T03 T08 T10 T11 T12 T15 T16 T17 T19, 1 run each | NONE | 9/10 (T10 0/1) |
| 1: same | OLD(pre) | 9/10 (T10 0/1) |
| 1: same | LENS | 8/10 (T10 0/1; also failed T17) |
| 2: same ten | STUB v1 | 10/10 (T10 3/3) |
| 2: T10 only | NONE | T10 0/2 more runs |
| 3: T02 T04 T05 T06 T07 T09 T13 T14 T18 T20, plus T10 | STUB v2 | 10/10 (T10 2/2) |
| 3: same ten | NONE | 10/10 |
| 3: T10 only | OLD | T10 0/3 |
| 4: T01 T03 T08 T11 T12 T15 T16 T17 T19, 1 run each | STUB v2 | 9/9 |

Totals over 20 cases, 82 runs:

- STUB v2: 20/20 cases, T10 2/2 runs. STUB v1 (144 words) also passed 10/10 on
  the first ten, T10 3/3. SHIPPED (209 words): 4/4 on T15, T17, T18, T20, the
  four cases whose Report items the two clarifications touch.
- NONE: 19/20 cases, T10 0/3 runs.
- OLD (a0d0266 body): measured only on T10, 0/3. The other 19 are
  extrapolated from OLD(pre) 9/10 and NONE 10/10, because OLD(pre) and NONE
  agreed on every one of the first ten cases.
- LENS failed T17 by making "the row is the unit of accounting" an
  unconditional rule instead of the parameterized-declaration counting rule.

## Decision

The stub is kept because one sentence, "the audit is complete when its
findings are reported", moves T10 from 0/6 runs (NONE 3, OLD 3) to 5/5 (v1 3,
v2 2). The a0d0266 body was measured only on T10 (0/3). That its other
sentences add nothing measurable on the other 19 cases is an extrapolation:
OLD(pre) 9/10 equals NONE 9/10 on the first ten, and NONE passed 10/10 on the
second ten. The retained paragraphs (unit of accounting, red tests, sweeps pointer,
report shape) stay because the owner's user-level rules leave them open, not
because they were measured. Old heuristics dropped without measurement: names
or fixtures that promise more than the input exercises, "static or slow is not
a deletion reason", non-test callers of a covered seam, not editing while a test
process runs, negative controls that pass via an unrelated guard, and
red-before-green for a new regression test.

## Confounds

- Synthetic cases written by the stub's author.
- T10 is tied to a rubric item the stub restates.
- One run per arm per case, except T10.
- Agents were Claude subagents, not Codex.
- Agents carried the owner's user-level rules in every arm, so NONE already
  includes those rules.

## 2026-10-17 PORTABLE replay

Question: do the cases that exercise the Tests rules still pass when those
rules come only from `test-audit/tests-rules.md`, with no user-level rules
loaded? Arm PORTABLE: fresh headless `claude -p --setting-sources project`
sessions from an empty directory whose `.claude/skills/test-audit` links to the
skill; the evaluation request of `cases.json` plus one case. Skill-load witness:
a `Skill` call for `test-audit` in each run's stream-json trace. Grading: one
blind headless grader per case, the rubric's Accept items as Minimum and Reject
items as Fail lines, one verdict file per run (18 files, no duplicates by md5).

| Case | PORTABLE runs passed | Runs with the skill loaded | Runs that read tests-rules.md | Baseline STUB v2, 2026-10-14 (runs passed) |
| --- | --- | --- | --- | --- |
| T03 | 3/3 | 3/3 | 3/3 | 1/1 |
| T06 | 3/3 | 3/3 | 3/3 | 1/1 |
| T07 | 3/3 | 3/3 | 3/3 | 1/1 |
| T09 | 3/3 | 3/3 | 3/3 | 1/1 |
| T13 | 3/3 | 3/3 | 3/3 | 1/1 |
| T10 (control) | 3/3 | 3/3 | 3/3 | 2/2 |

Not worse on every case. Confounds: the grader ran headless rather than as a
subagent; synthetic cases as above; the evaluation request names the skill, so
the load witness says the skill body and rules reached the agent, not that its
description triggers it.
