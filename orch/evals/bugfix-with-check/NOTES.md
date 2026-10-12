Why: a one-screen bug spec with a `check:` line; it goes through the flow (builder, two Fable
reviews, cross-check) and the declared check must run (a Bash call with `bash check.sh`,
`make check` or `check-run`). The scaffold injects the bug: `remove` returns True for an
unknown id. The steps grader cannot prove the check ran after the last edit; the result grader
on `check-output.txt` covers that the final state passes.
