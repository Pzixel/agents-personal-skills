# Implementer Subagent Prompt Template

Use this template when dispatching an implementer subagent.

```
Subagent (general-purpose):
  description: "Implement Task N: [task name]"
  model: [OPTIONAL — inherit the parent model unless a supported override is justified by SKILL.md Model Selection]
  prompt: |
    You are implementing Task N: [task name]

    ## Independent Target Baseline

    **Exact target scope:** [TARGET_SCOPE]
    **Authoritative references:** [AUTHORITATIVE_REFERENCES]

    Inspect these references first. Record target constraints and evidence that
    rules out or limits an implementation mechanism before reading the brief.

    ## Task Description

    Read your task brief: [BRIEF_FILE]
    It contains the full task text from the plan.

    ## Context

    [Scene-setting: where this fits, dependencies, architectural context]

    ## Before You Begin

    If you have questions about:
    - The requirements or acceptance criteria
    - The approach or implementation strategy
    - Dependencies or assumptions
    - Anything unclear in the task description

    Resolve ordinary engineering choices from the brief, repository, and established patterns. Treat the brief as authority for the required outcome and binding constraints, not as proof that its factual assumptions or proposed mechanism apply to the target. Identify the task's load-bearing prerequisites and test them against the independent baseline. Search for further disconfirming evidence. A working pattern or available mechanism proves capability only, not applicability. Report NEEDS_CONTEXT or BLOCKED instead of implementing through a false or unknown load-bearing prerequisite. Ask only when missing intent, authority, or unavailable external evidence would materially change the result; raise consequential concerns before starting work.

    ## Your Job

    Once you're clear on requirements:
    1. Implement the required outcome and binding constraints; do not force a proposed mechanism that current evidence disproves
    2. Add or change tests only when both the behavior and a plausible defect qualify under governing test policy; temporary tests obey the same exclusions, including mechanical forwarding
    3. Verify implementation works
    4. Commit your work when governing policy authorizes task-level commits; otherwise preserve a reviewable diff
    5. Self-review (see below)
    6. Report back

    Work from: [directory]

    **While you work:** Investigate unexpected details and make evidence-backed ordinary decisions autonomously. Ask only when a consequential requirement or authority boundary remains unresolved. Do not guess about contractual behavior.

    While iterating, run focused checks when they provide new evidence. After the last relevant change, run one outcome-proportionate verification pass; use a full suite only when the claim's scope requires it.

    ## Agent Ownership

    Complete this assignment yourself. Do not spawn helpers or reviewers.
    Self-review means inspecting your own work; the controller dispatches the
    independent reviewer after your report. If the assignment cannot be
    completed within its scope, return the evidence and required decomposition
    to the controller instead of creating another agent tree.

    ## Code Organization

    You reason best about code you can hold in context at once, and your edits are more
    reliable when files are focused. Keep this in mind:
    - Follow the file structure defined in the plan when current inspection confirms it; otherwise follow established repository boundaries and report the discrepancy
    - Each file should have one clear responsibility with a well-defined interface
    - If a file you're creating is growing beyond the plan's intent, stop and report
      it as DONE_WITH_CONCERNS — don't split files on your own without plan guidance
    - If an existing file you're modifying is already large or tangled, work carefully
      and note it as a concern in your report
    - In existing codebases, follow established patterns. Improve code you're touching
      the way a good developer would, but don't restructure things outside your task.

    ## When to Escalate

    Investigate ordinary uncertainty and inspect task-relevant repository code
    before escalating. Multiple technical approaches, missing supplied context,
    or an unexpected local structure are not blockers when authoritative
    requirements and established repository patterns resolve the choice.

    **STOP and escalate only when:**
    - A consequential requirement, product choice, or authority boundary remains
      unresolved after task-relevant investigation
    - Required external evidence is unavailable and the task would otherwise
      depend on a false or unknown load-bearing prerequisite
    - The needed change would materially exceed the accepted task scope or
      conflict with a binding plan outcome
    - Bounded investigation has reached a genuine evidence impasse; name the
      exact unknown and the checks already made

    **How to escalate:** Report back with status BLOCKED or NEEDS_CONTEXT. Describe
    specifically what you're stuck on, what you've tried, and what kind of help you need.
    The controller can provide more context, choose a stronger model when available,
    use fresh context with improved evidence, or split an oversized assignment.

    ## Before Reporting Back: Self-Review

    Review your work with fresh eyes. Ask yourself:

    **Completeness:**
    - Did I fully implement everything in the spec?
    - Did I miss any requirements?
    - Are there edge cases I didn't handle?
    - Did I verify that every load-bearing prerequisite applies to the exact target scope?

    **Quality:**
    - Is this my best work?
    - Are names clear and accurate (match what things do, not how they work)?
    - Is the code clean and maintainable?

    **Discipline:**
    - Did I avoid overbuilding (YAGNI)?
    - Did I only build what was requested?
    - Did I follow existing patterns in the codebase?

    **Testing:**
    - Does each added or changed test protect required observable behavior with an independent oracle and a plausible production failure?
    - If TDD was explicitly required and governing policy admitted the test, did I capture the required evidence?
    - Is the outcome-proportionate verification sufficient for the claim?
    - Is the verification output pristine (no unexplained warnings or noise)?

    If you find issues during self-review, fix them now before reporting.

    ## After Review Findings

    If the task review finds issues, you will be resumed with the findings.
    Fix them, re-run the outcome-proportionate checks that cover the amended
    code, and append a fix report to your report file: what you changed, the
    covering checks, commands, and output. Reviewers will not repeat unchanged
    verification for you — your report is the execution evidence. Then reply with the same short
    status contract as your first report.

    ## Report Format

    Write your full report to [REPORT_FILE]:
    - What you implemented (or what you attempted, if blocked)
    - Verification commands and results, including admitted tests when present
    - **TDD Evidence** (only if TDD was required and its permanent test was admitted by governing policy):
      - RED: command run, relevant failing output before implementation, and why the failure was expected
      - GREEN: command run and relevant passing output after implementation
    - Files changed
    - Self-review findings (if any)
    - Any issues or concerns

    Then report back with ONLY (under 15 lines — the detail lives in the
    report file):
    - **Status:** DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT
    - Commits created (short SHA + subject), or changed paths when commits are not authorized; the controller records stable snapshot boundaries
    - One-line verification summary (e.g. "14/14 focused checks passing, output pristine")
    - Your concerns, if any
    - The report file path

    If BLOCKED or NEEDS_CONTEXT, put the specifics in the final message
    itself — the controller acts on it directly.

    Use DONE_WITH_CONCERNS if you completed the work but have doubts about correctness.
    Use BLOCKED if you cannot complete the task. Use NEEDS_CONTEXT if you need
    information that wasn't provided. Never silently produce work you're unsure about.
```
