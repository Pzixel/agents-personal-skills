Why: a narrow why-does-it-fail question whose reads stay under the orchestrate size
threshold (`main_budget.delegate_above_result_tokens` in `orch/tools/orchestrate-params.json`;
the fixture is about 7 KB) is read by the main session itself, with no `orch:scout` or
`orch:prod-probe` spawn, and answered without a fix. The scaffold flips the even-count median
expectation from 2.5 to 3.0, so the real cause is the test's expected value, not the code;
the llm grader checks the exact assertion, the cause, confirmed-vs-inferred and no code change.
