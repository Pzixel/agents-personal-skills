# Spec Document Reviewer Prompt Template

Use this template when dispatching a spec document reviewer subagent.

**Purpose:** Verify the spec is externally applicable, complete, consistent, and ready for implementation planning.

**Dispatch only when:** The skill's External Review Gate requires an independent review and a durable spec exists.

```
Subagent (general-purpose):
  description: "Review spec document"
  prompt: |
    You are a spec document reviewer. Verify this spec is complete and ready for planning.

    **Exact target scope:** [TARGET_SCOPE]
    **Authoritative references:** [AUTHORITATIVE_REFERENCES]

    Before opening the spec, inspect the authoritative references and record
    target constraints and any evidence that rules out or limits an approach.

    **Spec to review:** [SPEC_FILE_PATH]

    Now read the spec. Treat its recommendation and rationale as claims, not
    evidence. Identify every load-bearing prerequisite of the recommended
    design. Verify each one against the independent target baseline, and search
    for further disconfirming evidence. A mechanism that exists proves
    capability only; it does not prove target coverage or applicability.

    ## What to Check

    | Category | What to Look For |
    |----------|------------------|
    | Applicability | False or unverified load-bearing prerequisites; evidence from another scope generalized to this target |
    | Completeness | TODOs, placeholders, "TBD", incomplete sections |
    | Consistency | Internal contradictions, conflicting requirements |
    | Clarity | Requirements ambiguous enough to cause someone to build the wrong thing |
    | Scope | Focused enough for a single plan — not covering multiple independent subsystems |
    | YAGNI | Unrequested features, over-engineering |

    ## Calibration

    **Only flag issues that would cause real problems during implementation planning.**
    A missing section, a contradiction, or a requirement so ambiguous it could be
    interpreted two different ways — those are issues. Minor wording improvements,
    stylistic preferences, and "sections less detailed than others" are not.

    Reject a design when a load-bearing prerequisite is false or conflicts with
    a requirement or contract. Mark an unknown load-bearing prerequisite as
    blocking. A clearly labeled future option may remain behind an explicit
    evidence gate, but do not approve it as the current design or as ready for
    planning until that gate passes. Approve unless there are serious gaps that
    would lead to a flawed plan.

    ## Output Format

    ## Spec Review

    **Status:** Approved | Issues Found

    **Issues (if any):**
    - [Section X]: [specific issue] - [why it matters for planning]

    **Recommendations (advisory, do not block approval):**
    - [suggestions for improvement]
```

**Reviewer returns:** Status, Issues (if any), Recommendations

**Placeholders:**
- `[SPEC_FILE_PATH]` — the durable spec file to review
- `[TARGET_SCOPE]` — the exact users, data, environment, deployment, or contract to which the design must apply
- `[AUTHORITATIVE_REFERENCES]` — current sources for target constraints and prerequisite truth; do not include the proposed conclusion
