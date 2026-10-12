Why: a narrow why-does-it-fail question is delegated to one `orch:Explore` or `orch:prod-probe` and
answered without a fix. The scaffold flips the even-count median expectation from 2.5 to
3.0, so the real cause is the test's expected value, not the code; the llm grader checks
the exact assertion, the cause, confirmed-vs-inferred and no code change.
