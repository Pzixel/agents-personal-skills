---
name: superpowers-writing-plans-psilon
description: 'Use for durable plans spanning 3+ stages, component/interface coordination, migrations/rollouts, cross-session handoff, risky order/recovery, or explicit written-plan request. Skip one-session work without coordination risk; brainstorm unresolved consequential design. Use a verified rolling horizon: detail next risky milestone; leave unproved later work as outcomes. Never invent detail or mistake capability for exact-target applicability.'
---

# Writing Plans

Write for a capable engineer with the repository but no session history. Save where requested; otherwise `docs/superpowers/plans/YYYY-MM-DD-<feature-name>.md`.

If consequential product semantics, success criteria, state ownership, or architecture is unresolved and user-owned, stop and apply `superpowers-brainstorming-psilon`. If the outcome is fixed but feasibility is unknown, plan a Discovery milestone with its evidence, decision rule, and fallback instead of guessing downstream design.

## Rolling Horizon

- Detail the next irreversible, coordinated, or risky milestone enough to execute.
- Keep later milestones to outcomes, dependencies, invariants, acceptance, and recovery while earlier gates can change them.
- Expand later work when evidence stabilizes it; detail every stage only when the whole path is verified.
- Do not freeze an API, storage layout, file set, benchmark size, rollout order, or UI before its design and feasibility gates pass.
- Verify each load-bearing prerequisite for the exact target; keep an unverified one behind an explicit gate, never as planned implementation.
- When evidence disproves an assumption, update the plan, record the decision and reason, and remove the superseded instructions.

An exact file, symbol, schema, command, or value belongs in the plan only when it is contractual, verified and needed for coordination, a fragile safety or migration step, or a shared interface. Never invent counts, timings, IDs, or expected output.

A task is an outcome; never a task for a file, test, commit, reviewer, or tool. Include code only when its literal text is the contract.

## Plan Skeleton

```markdown
# [Feature Name] Implementation Plan

**Goal:** [Observable outcome]
**Target scope:** [Exact users, data, environments, deployments, or contracts]
**Fixed constraints:** [Requirements and accepted decisions only]
**Authoritative references:** [Current specs, contracts, evidence, or runbooks]
**Current evidence:** [Verified facts that shape the approach]
**Open gates:** [Load-bearing unknowns only; omit when none]

## Milestone N: [Coherent outcome]

**Status:** Ready | Discovery | Provisional
**Depends on:** [Prior outcome or evidence gate]
**Scope:** [Verified boundaries; label candidates]
**Constraints and interfaces:** [Fixed or explicitly provisional items]
**Work:** [Detail justified by the current horizon]
**Acceptance:** [Observable result and its proof]
**Decision gate and fallback:** [Required for Discovery/Provisional]
```

For multi-session work add short `Progress`, `Decisions`, and `Discoveries` sections that record only what changes later work.

Replace every `TODO`, `TBD`, "handle edge cases", or "write tests" without behavior and oracle with a discovery: question, evidence, decision rule, fallback.
