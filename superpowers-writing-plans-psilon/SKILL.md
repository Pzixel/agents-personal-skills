---
name: superpowers-writing-plans-psilon
description: 'Use whenever the user asks for a plan, a revised plan, or an approach before work starts ("plan", "produce the plan", "how would you do it"), and for work spanning 3+ stages, migrations or rollouts, or cross-session handoff. Sets the plan shape: short approach first, detailed plan only after approval, a reviewed plan before any code, a verified rolling horizon that details only the next risky milestone. Skip one-step answers. Never invent detail or mistake capability for exact-target applicability.'
---

# Writing Plans

Write for a capable engineer with the repository but no session history. Save where requested; otherwise `docs/superpowers/plans/YYYY-MM-DD-<feature-name>.md`.

If consequential product semantics, success criteria, state ownership, or architecture is unresolved and user-owned, stop and resolve it with the owner first. If the outcome is fixed but feasibility is unknown, plan a Discovery milestone with its evidence, decision rule, and fallback instead of guessing downstream design.

## Plan Shape

- Every plan text sent to ExitPlanMode, the short approach included, opens with three lines, labels as written and values in the language the owner reads: `Task:` (what is made and what the requester will do with the result), `Done when:` (the one observation, at whoever uses the result, that shows it does that job) and `Existing:`. `Existing:` says what the project already has for each thing this plan defines, counts or builds (a term, a metric, a mechanism, a screen). Start from the word as the owner and the screens use it, not from a name coined in this plan. Find where the project fixes it: the decision or semantics record that the repository's AGENTS.md names, searched by the owner's word and by the data source, then the code that shows it; cite path:line. Then one of: `as is`; `changed for all: <every place that changes, in this plan>`; `new and distinct: <a name that does not contain the old one>, differs by <the fact, with a number>`. A meaning changed only inside the new work is none of the three. `Existing: none` only with the words searched and the files read. A plan that defines nothing new answers `Existing: none` with the files read. A value may be a list under its label. The owner approves the plan by these lines; `plan-simplify-gate` refuses ExitPlanMode without them.
- A requested plan comes as a short approach first: problem, solution, shape. The detailed plan follows only after the approach is approved.
- Run `/simplify` on every work plan right after it is written, before any code. The main session runs it, never a subagent: a plan below `plan_simplify.fan_out_min_words` (orch params) may take one agent applying all five angles (reuse, simplification, efficiency, altitude, premises); a larger plan takes at least `plan_simplify.fan_out_min_agents` parallel `orch:reviewer` agents by lens, spawned from the main session in one message, each prompt naming the plan path. A subagent asked to write a plan returns it with the fan-out request and does not run the lenses. Rework the plan from the findings, then build. Premises: find counts, timings, files, rollout order, or load-bearing prerequisites that no verified source backs; name the source, the probe that decides, or the gate the item waits behind. A pass on finished code is too late.

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

Task: [what is made and what the requester will do with the result, in plain words]
Done when: [the one observation, at whoever uses the result, that shows it does that job]
Existing: [for each term, metric, mechanism or screen the plan defines: the record and code that fix it today (path:line), then `as is`, `changed for all: ...` or `new and distinct: ..., differs by ...`; or `none` with the words searched and the files read]

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
