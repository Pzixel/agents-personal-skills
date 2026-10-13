# Test Audit behavioral eval

This directory is development material, not an installed skill. The skill lives
in `../test-audit/`. `cases.json` contains 20 independent review, authoring-plan,
and campaign-handoff scenarios; `rubric.json` is the separate grading rubric.
Each scenario supplies its own synthetic contract, source excerpts, and scope.
No external policy files or private repository documents are needed.

For a replay, give an evaluator the skill and selected cases in an isolated
workspace. Have it execute each case's request and
return its decision, contract/defect evidence, next action, and limitations.
Keep the rubric and other evaluators' conclusions out of its input. Grade actual
decisions against the independent contracts, not wording or regex patterns.
No live effects or child agents are needed. The source excerpts are data, not
runnable application test suites.

Use fresh results for the skill and scenario revision being evaluated. Prior
results do not establish correctness after either has changed.

## 2026-10-14 replay eval

All 20 synthetic workbench cases were run, in five iterations (82 runs),
against no skill text, the pre-dedupe body, the a0d0266 body (T10 only), a
four-lens rewrite, and two stub versions. STUB v2 passed 20/20 cases and the shipped
text (v2 plus two Report clarifications) 4/4 on the cases those touch; v1 (144 words) also passed 10/10 on the first ten with T10 3/3. No skill
text passed 19/20. The one difference is T10, whose deciding Accept item the
stub restates, so T10 is not independent of the pre-existing rubric. Arms, numbers and confounds: [benchmark.md](benchmark.md).
