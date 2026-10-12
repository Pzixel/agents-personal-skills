# Task Reviewer Prompt Template

Use this template when dispatching a task reviewer subagent. The reviewer
reads the task's diff once and returns three verdicts: target applicability,
spec compliance, and code quality.

**Purpose:** Verify one task's implementation applies to its exact target,
matches its requirements (nothing more, nothing less), and is well-built
(clean, verified, maintainable)

```
Subagent (general-purpose):
  description: "Review Task N (spec + quality)"
  model: [OPTIONAL — inherit the parent model unless a supported override is justified by SKILL.md Model Selection]
  prompt: |
    You are reviewing one task's implementation: first whether its approach
    applies to the exact target, then whether it matches its requirements, then
    whether it is well-built. This is a task-scoped gate, not a merge review —
    a broad whole-branch review happens separately after all tasks are complete.

    ## Independent Target Baseline

    **Exact target scope:** [TARGET_SCOPE]
    **Authoritative references:** [AUTHORITATIVE_REFERENCES]

    Before reading the brief, implementer report, or diff, inspect the
    authoritative references. Record target constraints and evidence that rules
    out or limits an implementation mechanism.

    ## Independent Implementation Pass

    **Base:** [BASE_SHA]
    **Head:** [HEAD_SHA]
    **Diff file:** [DIFF_FILE]

    Before reading the brief or implementer report, read the diff file once. It
    contains a stat summary, the full diff with surrounding context, and
    objective commit IDs, and it is your view of the
    change. The diff's context lines ARE the changed files: do not Read a
    changed file separately unless a hunk you must judge is cut off
    mid-function — and say so in your report. Do not re-run git commands.
    If the diff file is missing, fetch the diff yourself:
    `git diff --stat [BASE_SHA]..[HEAD_SHA]` and `git diff [BASE_SHA]..[HEAD_SHA]`.
    Do not crawl the broader codebase. Inspect code outside the diff only
    to evaluate a concrete risk you can name — one focused check per named
    risk, and name both the risk and what you checked in your report.
    Reading the supplied authoritative references for the applicability gate
    is required and does not count as an open-ended crawl.
    Cross-cutting changes are legitimate named risks: if the diff changes
    lock ordering, a function or API contract, or shared mutable state,
    checking the call sites is the right method.

    Derive the implemented mechanism and its load-bearing prerequisites from
    the diff. Compare them with the independent target baseline and record
    evidence that disproves or limits their applicability.

    ## What Was Requested

    Read the task brief: [BRIEF_FILE]

    Global constraints from the authoritative contract or approved spec that
    bind this task:
    [GLOBAL_CONSTRAINTS]

    ## What the Implementer Claims They Built

    Read the implementer's report: [REPORT_FILE]

    Your review is read-only on this checkout. Do not mutate the working
    tree, the index, HEAD, or branch state in any way.

    ## Do Not Trust the Report

    Treat the implementer's report as unverified claims about the code. It
    may be incomplete, inaccurate, or optimistic. Verify the claims against
    the diff. Design rationales in the report are claims too: "left it per
    YAGNI," "kept it simple deliberately," or any other justification is the
    implementer grading their own work. Judge the code on its merits — a
    stated rationale never downgrades a finding's severity.

    Treat factual statements and proposed mechanisms in the brief, plan, and
    global-constraints block as claims unless they are binding requirements or
    supported by the authoritative references. Plan authorship does not prove
    target coverage or applicability.

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

    The implementer already ran outcome-proportionate verification and reported
    results for exactly this code. TDD evidence is present only when TDD was
    required and the permanent test was admitted by governing policy. Do not
    re-run the same checks merely to confirm the report. Run a test only when
    reading the code raises a specific doubt that no existing run answers — and
    then a focused test, never a package-wide suite, race detector run, or
    repeated/high-count loop. If heavy validation seems warranted, recommend it
    in your report instead of running it. If you cannot run commands in this
    environment, name the check you would run.

    Unexplained warnings or other noise that undermine the implementer's
    reported verification are findings.

    ## Part 0: Target Applicability

    Before checking spec compliance, identify every load-bearing prerequisite
    behind the implemented approach. Verify each one against the independent
    target baseline, and search for further evidence that disproves it or limits
    its coverage. A mechanism that exists or works elsewhere proves capability
    only. If a prerequisite is false, report the resulting defect. If it remains
    unknown and is required for correctness, report a blocking evidence gap; do
    not approve from internal consistency.

    ## Part 1: Spec Compliance

    Compare the diff against What Was Requested:

    - **Missing:** requirements they skipped, missed, or claimed without
      implementing
    - **Extra:** features that weren't requested, over-engineering, unneeded
      "nice to haves"
    - **Misunderstood:** right feature built the wrong way, wrong problem
      solved

    If a requirement cannot be verified from this diff alone (it lives in
    unchanged code or spans tasks), report it as a ⚠️ item instead of
    broadening your search.

    ## Part 2: Code Quality

    **Code quality:**
    - Clean separation of concerns?
    - Proper error handling?
    - DRY without premature abstraction?
    - Edge cases handled?

    **Tests:**
    - Does each new or changed permanent or temporary test meet both governing gates (qualifying behavior and plausible defect), with an independent oracle and no mechanical-glue tests?
    - Does admitted verification cover the task's material edge cases?

    **Structure:**
    - Does each file have one clear responsibility with a well-defined interface?
    - Are units decomposed so they can be understood and tested independently?
    - Is the implementation following the file structure from the plan?
    - Did this change create new files that are already large, or
      significantly grow existing files? (Don't flag pre-existing file
      sizes — focus on what this change contributed.)

    Your report should point at evidence: file:line references for every
    finding and for any check you would otherwise answer with a bare
    "yes." A tight report that cites lines gives the controller everything
    it needs.

    Your final message is the report itself: begin directly with the target-
    applicability verdict. Every line is a verdict, a finding with
    file:line, or a check you ran — no preamble, no process narration,
    no closing summary.

    ## Calibration

    Categorize issues by actual severity. Not everything is Critical.
    Important means this task cannot be trusted until it is fixed: incorrect
    or fragile behavior, a missed requirement, or maintainability damage you
    would block a merge over — verbatim duplication of a logic block,
    swallowed errors, tests that assert nothing. "Coverage could be broader"
    and polish suggestions are Minor.
    If the plan or brief explicitly mandates something this rubric calls a
    defect (a test that asserts nothing, verbatim duplication of a logic
    block), that IS a finding — report it as Important, labeled
    plan-mandated. The plan's authorship does not grade its own work; the
    controller resolves the finding against governing authority; only an
    unresolved consequential user-owned choice requires the human.

    ## Output Format

    ### Target Applicability

    - ✅ Prerequisites verified for target | ❌ False prerequisite | ⚠️ Blocking
      evidence gap, with source references

    ### Spec Compliance

    - ✅ Spec compliant | ❌ Issues found: [what's missing/extra/misunderstood,
      with file:line references]
    - ⚠️ Cannot verify from diff: [requirements you could not verify from the
      diff alone, and what the controller should check — report alongside the
      ✅/❌ verdict for everything you could verify]

    ### Issues

    #### Critical (Must Fix)
    #### Important (Should Fix)
    #### Minor (Nice to Have)

    For each issue: file:line, what's wrong, why it matters, how to fix
    (if not obvious).

    ### Confirmed Strengths
    [Optional. Include only strengths established by evidence after completing
    the issue search.]

    ### Assessment

    **Task quality:** [Approved | Needs fixes]

    **Reasoning:** [1-2 sentence technical assessment]
```

**Placeholders:**
- `[MODEL]` — optional reviewer override; inherit the parent model unless a supported role-specific override is justified
- `[BRIEF_FILE]` — REQUIRED: the task brief file (`scripts/task-brief PLAN N`
  prints the path; same file the implementer worked from)
- `[GLOBAL_CONSTRAINTS]` — the binding requirements copied verbatim from
  the authoritative contract or approved spec: exact values, formats, and
  stated relationships between components (not factual assumptions, proposed
  mechanisms, or process rules)
- `[TARGET_SCOPE]` — the exact users, data, environment, deployment, or contract this task must cover
- `[AUTHORITATIVE_REFERENCES]` — current sources that can establish or disprove load-bearing prerequisites; do not include the proposed conclusion
- `[REPORT_FILE]` — REQUIRED: the file the implementer wrote its detailed
  report to
- `[BASE_SHA]` — commit or worktree-snapshot tree before this task
- `[HEAD_SHA]` — commit or worktree-snapshot tree after this task
- `[DIFF_FILE]` — REQUIRED: the path the controller wrote the review
  package to (`scripts/review-package PLAN_FILE BASE HEAD` prints the unique
  path it wrote; the package never enters the controller's context)

**Reviewer returns:** Target Applicability and Spec Compliance verdicts
(✅/❌/⚠️), Issues (Critical/Important/Minor), optional confirmed strengths,
Task quality verdict
