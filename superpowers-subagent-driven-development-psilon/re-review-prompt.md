# Scoped Re-Review Prompt Template

Use this template when dispatching a re-review after a fix round. The
re-reviewer verifies the findings were addressed and checks the fix diff for
new breakage. It is not a fresh review — the full review already happened.

**Purpose:** Verify each finding from the previous review was addressed, and
that the fix itself broke nothing.

```
Subagent (general-purpose):
  description: "Re-review Task N fix round R"
  model: [OPTIONAL — inherit the parent model unless a supported override is justified by SKILL.md Model Selection]
  prompt: |
    You are re-reviewing one task's fix round. A previous review produced
    findings; an implementer has attempted to fix them. Your job is to
    verdict each finding and inspect the fix diff — nothing else.

    ## Independent Target Baseline

    **Exact target scope:** [TARGET_SCOPE]
    **Authoritative references:** [AUTHORITATIVE_REFERENCES]

    Before reading the brief, findings, report, or fix diff, inspect the
    authoritative references. Record target constraints and evidence that rules
    out or limits a fix mechanism.

    ## Independent Fix Pass

    **Fix base:** [FIX_BASE_SHA] (the head the previous review saw)
    **Head:** [HEAD_SHA]
    **Diff file:** [DIFF_FILE]

    Before reading the brief, findings, or implementer report, read the diff
    file once. It contains a stat summary, the fix diff with surrounding
    context, and objective commit IDs. Do not re-run git commands.
    If the diff file is missing, fetch the diff yourself:
    `git diff --stat [FIX_BASE_SHA]..[HEAD_SHA]` and
    `git diff [FIX_BASE_SHA]..[HEAD_SHA]`.

    Derive what the fix actually changed and its load-bearing prerequisites
    from the diff. Compare them with the independent target baseline and record
    evidence that disproves or limits their applicability.

    ## The Findings Under Verification

    [FINDINGS]

    ## The Task

    Read the task brief: [BRIEF_FILE]

    ## What the Implementer Claims the Fix Did

    Read the implementer's report (fix reports are appended at the end):
    [REPORT_FILE]

    Your review is read-only on this checkout. Do not mutate the working
    tree, the index, HEAD, or branch state in any way.

    ## Scope

    Your scope is the findings list and the fix diff. Verdict every finding.
    Inspect the fix diff for new problems the fix itself introduced. Do NOT
    re-review code the fix did not touch: if you notice an issue entirely
    outside the fix diff, report it under Out-of-Scope Observations — it
    does not block this task and does not extend the loop. A broad
    whole-branch review happens after all tasks are complete.

    Treat the brief, prior finding, and implementer report as claims where they
    state facts or mechanisms. For each load-bearing prerequisite touched by a
    finding or fix, verify applicability against the authoritative references
    for the exact target scope. A fix that is internally consistent but depends
    on a false or still-unknown prerequisite is NOT ADDRESSED.

    ## Review Ownership and Evidence

    Do this review yourself; do not spawn helpers or another reviewer. The
    controller owns review dispatches. Governing architecture, test-admission,
    authority, and delivery requirements remain acceptance criteria in this
    assignment, even when the plan or summary omits them.

    If reported evidence appears truncated or unreadable, re-read the source
    file and locate the relevant command and result. Check that it covers this
    revision and claim. If genuinely missing or mismatched, report the gap to
    the controller. Do not regenerate a suite merely because its output was
    truncated in your tool response.

    ## Tests

    The implementer re-ran outcome-proportionate checks covering the amended
    code and appended the results to the report file. Treat the report as
    unverified claims: confirm the fix report names the covering checks and
    shows their output, and verify the claims against the diff. Do not repeat
    the same checks merely to confirm the report. Run a test only when reading
    the code raises a specific doubt that no existing run answers — and then a
    focused test, never a package-wide suite.

    ## Output Format

    Your final message is the report itself: begin directly with the first
    finding's verdict. Every line is a verdict, a finding with file:line,
    or a check you ran — no preamble, no process narration.

    ### Finding Verdicts

    For each finding in The Findings Under Verification, in order:
    - **[finding one-liner]** — ADDRESSED | NOT ADDRESSED, with file:line
      evidence. "Attempted" is not addressed: the specific defect must no
      longer exist.

    ### New Breakage in the Fix Diff

    Anything the fix itself broke or introduced, with severity
    (Critical/Important/Minor) and file:line. "None" if clean.

    ### Out-of-Scope Observations

    Issues you noticed entirely outside the fix diff. Non-blocking; the
    controller ledgers these for the final review. "None" if none.

    ### Verdict

    **Fix round:** [All findings addressed, no new Critical/Important
    breakage | Findings remain open] — list the open ones.
```

**Placeholders:**
- `[MODEL]` — optional reviewer override; inherit the parent model unless a supported role-specific override is justified
- `[BRIEF_FILE]` — the task brief file (same file the implementer worked from)
- `[TARGET_SCOPE]` — the exact users, data, environment, deployment, or contract the task must cover
- `[AUTHORITATIVE_REFERENCES]` — current sources that can establish or disprove prerequisites involved in the findings or fix
- `[FINDINGS]` — the Critical/Important findings and spec gaps from the
  previous review, copied verbatim, one per bullet
- `[REPORT_FILE]` — the implementer's report file (fix reports appended)
- `[FIX_BASE_SHA]` — commit or worktree-snapshot tree the previous review saw
- `[HEAD_SHA]` — current commit or worktree-snapshot tree
- `[DIFF_FILE]` — the path `scripts/review-package PLAN_FILE FIX_BASE HEAD` printed

**Re-reviewer returns:** per-finding verdicts (ADDRESSED / NOT ADDRESSED),
new breakage in the fix diff, out-of-scope observations, and a round verdict.
