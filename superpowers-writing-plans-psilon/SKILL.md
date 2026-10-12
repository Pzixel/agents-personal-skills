---
name: superpowers-writing-plans-psilon
description: 'Use for durable plans spanning 3+ stages, component/interface coordination, migrations/rollouts, cross-session handoff, risky order/recovery, or explicit written-plan request. Skip one-session work without coordination risk; brainstorm unresolved consequential design. Use a verified rolling horizon: detail next risky milestone; leave unproved later work as outcomes. Never invent detail or mistake capability for exact-target applicability.'
---

# Writing Plans

> Preserve complex cross-session handoff, but admit detail only when evidence supports it. A plan is a living decision and coordination record, not speculative implementation prose.

Write for a capable engineer with the current repository and tools but no session history. Preserve outcomes, verified constraints, shared contracts, order, acceptance, rollout, and recovery. Omit facts that are cheap and safe to rediscover.

Announce: “I'm using the superpowers-writing-plans-psilon skill to create an evidence-backed implementation plan.” Follow governing policy. Save where requested; otherwise use `docs/superpowers/plans/YYYY-MM-DD-<feature-name>.md`.

## Establish the Boundary

Inspect only enough to separate:

- required outcome and external contracts;
- exact users, data, environment, deployment, or contract scope;
- verified repository/runtime facts;
- accepted decisions and rationale;
- implementation hypotheses needing evidence;
- unknowns that can change architecture, scope, or feasibility.

If consequential product semantics, success criteria, state ownership, or architecture remains user-owned and unresolved, stop and apply `superpowers-brainstorming-psilon`.

If outcome is fixed but feasibility is unknown, plan a discovery/prototype milestone: evidence, decision, promotion criteria, and fallback. Do not guess downstream design.

## Evidence Rules

Every load-bearing statement is one of:

1. **Requirement:** explicit intent, approved outcome, policy, or external contract.
2. **Verified fact:** inspected code, schema, dependency, runtime, or authority.
3. **Decision:** choice justified by requirements, facts, and material trade-offs.
4. **Provisional hypothesis:** labeled, with bounded validation and promote/redesign/stop criteria.

The final plan need not label every sentence, but every provisional load-bearing choice and gate must be visible. Unknowns are not blanks for plausible answers. State evidence scope and date when drift matters. Never turn correlation, a narrow benchmark or cohort, history, or one agent's preference into a contract.

For each selected approach, name prerequisites; verify them for the exact target; seek disconfirming and limiting evidence. APIs, parsers, integrations, examples, and prior success prove capability only. Reject any approach whose load-bearing prerequisite is false or conflicts with requirements or contracts. Keep unknown ones conditional behind an explicit gate, never as planned implementation.

## Rolling Horizon

- Detail the next irreversible, coordinated, or risky milestone enough to execute.
- Keep later milestones to outcomes, dependencies, invariants, acceptance, and recovery while earlier gates can change them.
- Expand later work when evidence stabilizes it; detail all stages only when the whole path is verified.
- A plan may stop at a decision boundary and state what evidence permits revision.

Do not freeze API, storage, files, benchmark size, rollout order, or UI before their design and feasibility gates pass.

When evidence disproves an assumption, update the plan and record the new decision and reason; remove superseded instructions. Requirements and authority still bind. Difficulty does not relax them, and history must not be rewritten.

## Admit Useful Detail Only

Include an exact file, symbol, type, schema, command, value, or code fragment only when it is contractual/approved, currently verified and needed for coordination, a fragile safety/migration/recovery/order instruction, or a shared cross-task interface. An exact command must also be necessary to prove its outcome. Otherwise name the owner, behavior, or result; label likely details as candidates; prefer symbols over line numbers.

Use complete code only when literal text is the contract or a small fragile sequence cannot be transferred safely. Never invent production counts, times, IDs, budgets, benchmark samples, or expected output. If measurement is not fixed, state risk and confidence needs; choose and justify the method in that milestone.

Give each fact one owner. Reference specs, contracts, runbooks, and evidence; repeat only the minimum safe constraint.

## Outcome-Based Work

Map verified ownership before tasks. Follow inspected repository boundaries; do not freeze speculative files or decomposition. Treat restructuring as a separate decision: require evidence that current boundaries cannot support the outcome or that restructuring has material cumulative value. A task is the smallest independently implementable, verifiable, and acceptable outcome. Fold setup, scaffolding, config, and docs into the outcome that needs them. Split only for a real dependency, handoff, deployment/recovery boundary, or independent acceptance gate.

Size steps by a checkable result. Spell out fragile stateful sequences where omission or reordering can fail. Never create tasks merely for files, routine edits, tests, commits, reviewers, or tools.

## Plan Form

Start with:

```markdown
# [Feature Name] Implementation Plan

> **Living plan:** Keep decisions and progress aligned with current evidence.
> Delegation and review apply only when their own triggers match.

**Goal:** [Observable outcome]
**Target scope:** [Exact users, data, environments, deployments, or contracts]
**Fixed constraints:** [Requirements and accepted decisions only]
**Authoritative references:** [Current specs, contracts, evidence, or runbooks]
**Current evidence:** [Verified facts that shape the approach]
**Open gates:** [Load-bearing unknowns only; omit when none]
```

Use only valuable sections:

```markdown
## Milestone N: [Coherent outcome]

**Status:** Ready | Discovery | Provisional
**Depends on:** [Prior outcome or evidence gate]
**Scope:** [Verified boundaries; label candidates]
**Constraints and interfaces:** [Fixed or explicitly provisional items]
**Work:** [Detail justified by the current horizon]
**Acceptance:** [Observable result and proportionate proof]
**Decision gate and fallback:** [Required for Discovery/Provisional]
```

For multi-session work, add concise `Progress`, `Decisions`, and `Discoveries`; record only changes that affect later work. Do not create a terminal log, duplicate runbook, or evidence warehouse. Include only verified project commands, authorized commit steps, and tests admitted by governing policy.

## Gates, Not Placeholders

Plan failures include `TODO`/`TBD`; “later” or “handle edge cases”; “write tests” without behavior and oracle; unverified exact-looking detail; provisional choices stated as final; downstream detail behind an open gate; or duplicated authoritative material.

Replace each with an actionable discovery: question, evidence, decision rule, and fallback. The executor must know how to learn the answer without the plan pretending to know it.

## One Self-Review

Before saving, fix once:

1. every requirement maps to an outcome and acceptance check without extra behavior;
2. every load-bearing detail has a requirement, fact, decision, or provisional gate;
3. prerequisites are exact-target verified with disconfirming evidence checked;
4. frozen producer/consumer names, types, formats, signatures, and owners agree within the current horizon—never audit provisional later interfaces as fixed;
5. guessed files, schemas, commands, values, cohorts, methods, and timing are removed or qualified;
6. later work is less detailed where earlier gates can change it;
7. specs, runbooks, policy, and evidence are referenced, not copied;
8. no subagent, reviewer, worktree, TDD, commit, or release ceremony exists without policy or a matching skill trigger;
9. a fresh executor can find current state, next milestone, and every open gate.

Check proportion: if the plan mostly repeats the spec or writes the future implementation, keep the decisions, shared interfaces, admitted test oracles, and acceptance checks; remove redundant bodies and prose. Length alone is not a defect when coordination or recovery requires the detail.

Do not dispatch review merely because a plan exists. For an explicit or independently justified high-risk review, use [plan-document-reviewer-prompt.md](plan-document-reviewer-prompt.md) with `[PLAN_FILE_PATH]`, `[TARGET_SCOPE]`, and `[AUTHORITATIVE_REFERENCES]`.

## Continue

Continue toward the requested outcome inline with native tracking and proportionate checkpoints. Use `superpowers-subagent-driven-development-psilon` only when the user explicitly chose that mode and its eligibility conditions hold; an accepted plan alone does not select it. Expand later milestones as gates clear. Ask only when a consequential user choice, external coordination, or authority boundary cannot be resolved safely.
