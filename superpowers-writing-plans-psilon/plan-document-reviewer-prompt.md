# Plan Document Reviewer Prompt Template

> Retained from upstream for an explicit or independently justified high-risk
> plan review. Do not dispatch it automatically.

Use this template only when independent review can materially reduce plan risk.

```
Subagent (general-purpose):
  description: "Review evidence-backed plan"
  prompt: |
    Review the plan as a living execution artifact for a complex task.

    **Exact target scope:** [TARGET_SCOPE]
    **Authoritative references:** [AUTHORITATIVE_REFERENCES]

    Before opening the plan, inspect the authoritative references and record
    target constraints and any evidence that rules out or limits an approach.

    **Plan:** [PLAN_FILE_PATH]

    Now read the plan. Treat its selected approach, factual statements, and
    rationale as claims, not evidence. Before checking internal completeness,
    identify every load-bearing prerequisite of the selected approach. Verify
    it against the independent target baseline, and search for further
    disconfirming evidence. A mechanism that exists proves capability only; it
    does not prove target coverage or applicability.

    Check only issues that could cause wrong implementation, blocked execution,
    unsafe sequencing, wasted large work, or a false completion claim.

    ## Review dimensions

    1. Outcome alignment
       - Required outcomes and constraints are covered.
       - The plan adds no unsupported product behavior or authority.

    2. Evidentiary grounding
       - Every load-bearing detail follows from a requirement, verified fact,
         justified decision, or explicit provisional gate.
       - Historical, narrow, or unrepresentative evidence is not generalized.
       - False prerequisites reject the approach; unknown load-bearing
         prerequisites remain explicit gates rather than planned facts.

    3. Interface consistency
       - Contracts frozen within the current planning horizon use the same
         names, types, formats, method signatures, and ownership at every
         producer and consumer milestone.
       - Provisional later interfaces are not treated as frozen contracts.

    4. False precision
       - Exact files, interfaces, schemas, commands, values, benchmark methods,
         and timings are verified or clearly provisional.
       - Unknowns are discovery gates rather than plausible-looking guesses.

    5. Rolling horizon
       - The next risky milestone is actionable.
       - Later milestones stay outcome-level when earlier evidence may change
         their architecture.

    6. Source ownership and safety
       - The plan references rather than duplicates specs, runbooks, and policy.
       - Ordering, recovery, rollback, and acceptance match the actual risk.
       - The plan does not mandate delegation or review ceremony merely because
         it exists.

    7. Restartability
       - A fresh executor can identify current state, next work, unresolved
         gates, and acceptance without session history.

    ## Output

    **Status:** Approved | Issues Found

    **Blocking issues:**
    - [location]: [unsupported or unsafe statement] - [evidence needed or fix]

    **Advisory simplifications:**
    - [detail that can be removed or deferred without reducing safety]

    Approve when no material issue remains. Do not request more prose, detail,
    tests, reviewers, or milestones unless it closes a named execution risk.
```
