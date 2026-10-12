---
name: superpowers-requesting-code-review-psilon
description: Use for one independent review after a coherent implementation crosses security, data-integrity, concurrency, migration, external-contract, production, or multi-component risk, or on explicit request. Skip review after every task by default, and merely-complete, low-risk, mechanical, or no-value review. Review read-only and evidence-first before release or completion.
---

# Requesting Code Review

> **Codex 5.6 adaptation:** The description is the scope gate. Review once at a meaningful risk boundary; repeat only for a distinct evidenced risk.

Dispatch an independent read-only reviewer with exact task context, never session history.

**Core principle:** Give the reviewer the target, binding requirements, current evidence, raw diff, and labeled implementation claims or plan. The reviewer inspects the diff first; claims and plans are second-pass context, never proof.

## Trigger

Use after a coherent high-risk implementation, before a release or merge that crosses a listed risk boundary, or on explicit request.

Use optionally only for a named uncertainty: a stuck investigation, risky-refactor baseline, or complex fix with uncertain integrated effects.

## Dispatch

1. Record the coherent range:

   ```bash
   BASE_SHA=$(git merge-base HEAD origin/main) # or recorded change start
   HEAD_SHA=$(git rev-parse HEAD)
   ```

2. Dispatch an isolated or minimally forked reviewer with [code-reviewer.md](code-reviewer.md):

   - `[TARGET_SCOPE]` — exact users, data, environment, deployment, or contract
   - `[AUTHORITATIVE_REQUIREMENTS]` — binding requirements and contracts
   - `[AUTHORITATIVE_REFERENCES]` — current sources that can prove or disprove prerequisites
   - `[DESCRIPTION]` — implementer's claimed result; label it as a claim
   - `[PLAN]` — optional decision record; never factual proof
   - `[DIFF_FILE]` — optional exact review package; blank uses the Git range
   - `[BASE_SHA]`, `[HEAD_SHA]` — exact review boundaries

3. Verify every finding against current code, requirements, and target evidence. Fix confirmed Critical issues at once and Important issues before proceeding; record Minor issues. Push back with evidence when the reviewer is wrong.

## Hard Rules

- Never skip review after the trigger truly matches.
- Never ignore Critical issues or proceed with confirmed Important issues open.
- Never give the reviewer session history or an implementation conclusion as fact.
- Never argue with valid feedback; challenge invalid feedback with code, checks, and technical reasoning.
- Independent review complements, not replaces, outcome-proportionate verification.

Template: [code-reviewer.md](code-reviewer.md)
