---
name: superpowers-receiving-code-review-psilon
description: Use for concrete user or external code-review feedback to assess, answer, or implement. Skip post-implementation review initiation. Verify diagnoses, facts, mechanisms, and exact-target prerequisites against current code and requirements; resolve consequential ambiguity and push back with evidence.
---

# Receiving Code Review

Split each item into the requested outcome, the reviewer's diagnosis, and the proposed mechanism. A binding user requirement binds; diagnoses and mechanisms are claims.

For each item:

1. Check the diagnosis and its facts against the current code and the exact target; look for evidence that disproves it, including the reason the existing behavior exists.
2. If it holds, fix it at its root cause; the reviewer's proposed mechanism is a candidate, not a requirement.
3. If it does not hold, push back with specific evidence (file:line, command output). If the evidence is missing, say exactly what is missing.

Handle blocking and security items first; group items that share a cause into one fix and run one regression pass after the last change.

Fixing feedback does not authorize replying. When replies are authorized, answer an inline GitHub review comment in its own thread (`gh api repos/{owner}/{repo}/pulls/{pr}/comments/{id}/replies`), not as a top-level PR comment.
