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
