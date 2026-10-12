---
name: superpowers-receiving-code-review-psilon
description: Use for concrete user or external code-review feedback to assess, answer, or implement. Skip post-implementation review initiation. Verify diagnoses, facts, mechanisms, and exact-target prerequisites against current code and requirements; resolve consequential ambiguity and push back with evidence.
---

# Receiving Code Review

Separate the requested outcome from the reviewer's diagnosis and proposed mechanism. User requirements and authority decisions bind; technical claims still need verification against the current code and exact target.

## Assess and Act

1. Read the feedback together and identify the governing requirements, affected behavior, and dependencies between items.
2. Check each claim against code, current target evidence, compatibility needs, and the reason for existing behavior. Seek evidence that disproves the diagnosis; a working mechanism elsewhere does not establish applicability here.
3. Correct confirmed defects within authority. Reject incorrect findings with specific evidence; resolve disputed evidence before dependent changes. Neither a reviewer nor a plan creates a requirement from a preference.
4. Verify each correction at its smallest meaningful boundary, then run one proportionate regression pass after the last relevant change. Apply governing test-admission rules to permanent and temporary tests alike.

For an unclear item, investigate before changing it or its dependents. Continue independent verified work that cannot constrain the unresolved decision. Prioritize blocking and security defects, then group the remaining work by dependencies and coherent corrections rather than forcing a review/fix/check cycle for every comment.

Ask only when consequential intent, authority, a conflict with a binding user decision, or unavailable required evidence cannot be resolved from the existing instructions and investigation. Do not add unused features merely because a reviewer calls them professional.

## Communicate the Result

State the verified change, the technical reason for disagreement, or the exact missing evidence. Correct an earlier mistaken conclusion briefly when new evidence disproves it. Acknowledgment is not proof or correction.

When replies are authorized, answer inline GitHub review comments in their own thread (`gh api repos/{owner}/{repo}/pulls/{pr}/comments/{id}/replies`), not as top-level PR comments. Reviewing or fixing feedback alone does not authorize sending a message.
