---
name: superpowers-brainstorming-psilon
description: Use before planning a non-trivial change when unresolved product intent or success could materially alter architecture, interfaces, persistent state, multi-component behavior, or long-term maintenance, or coupled subsystems need decomposition. Skip clear local, routine, mechanical, one-small-decision work and adequate designs. Investigate ordinary choices; verify exact-target prerequisites; compare admitted options; recommend or block.
---

# Brainstorming Ideas Into Designs

Do not implement or scaffold the product until the design is coherent and consequential product or authority decisions are resolved. A bounded feasibility experiment is allowed.

## Scope

If several independent subsystems exceed one spec, define their boundaries, links, and build order, then brainstorm only the first subproject.

## Candidate Admission

For each candidate approach:

- name every load-bearing prerequisite;
- verify it with current evidence for the exact target, not for a similar system or the capability in general;
- look for evidence that disproves it or limits its coverage;
- reject a candidate whose prerequisite is false or conflicts with requirements, contracts, or authority;
- keep a candidate with an unknown prerequisite conditional and exclude it from the recommendation.

A conditional mechanism may be a discovery subject, never the recommended production design.

## Feasibility Discovery

When the outcome is known but a load-bearing premise is not, state the question, the exact target, the smallest discriminating experiment, and the evidence that would admit, reject, or revise the candidate. Inspect read-only first; prototype only when inspection cannot answer. A prototype proves only the scope it exercised; never deploy it. Then rerun admission.

## Output

End with exactly one of:

1. **Recommendation:** two or three admitted approaches (or why only one is credible), the recommended one first with its trade-offs, and a design covering components, data flow, errors, and verification.
2. **Discovery step:** the experiment above, ready to run.
3. **Blocked:** the missing evidence or authority and the minimum clearance.

Use `superpowers-writing-plans-psilon` only when its durable-plan trigger matches.
