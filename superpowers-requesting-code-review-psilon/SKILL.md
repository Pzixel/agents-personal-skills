---
name: superpowers-requesting-code-review-psilon
description: Use for one independent review after a coherent implementation crosses security, data-integrity, concurrency, migration, external-contract, production, or multi-component risk, or on explicit request. Skip review after every task by default, and merely-complete, low-risk, mechanical, or no-value review. Review read-only and evidence-first before release or completion.
---

# Requesting Code Review

Dispatch one independent read-only reviewer with [code-reviewer.md](code-reviewer.md). Fill its brief: target scope, binding requirements and references, the review target (`<base>..<head>`, or a PR, branch, or path), and the implementer's claimed result labelled as a claim. Never pass session history, and never state an implementation conclusion as fact.

Review once at a risk boundary. Repeat only for a distinct evidenced risk.

Use optionally only for a named uncertainty: a stuck investigation, risky-refactor baseline, or complex fix with uncertain integrated effects.

## Handling the findings

The reviewer returns a JSON array of findings. Verify each one against the current code and the requirements. Fix confirmed findings before proceeding. When a finding is wrong, push back with evidence: file:line references or command output.
