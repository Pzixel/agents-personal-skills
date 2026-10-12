---
name: superpowers-dispatching-parallel-agents-psilon
description: Use after bounded scoping proves 2+ substantial workstreams have distinct deliverables, no sequential dependency, non-overlapping mutable state, and parallel work beats overhead. Skip SDD-plan tasks, coupled decisions, shared-path reconnaissance, related failures that may share a cause, trivial splits, overlapping writes, and automatic review. The primary owns integration; never infer independence from files, symptoms, labels, or free slots.
---

# Dispatching Parallel Agents

> **Codex 5.6 adaptation:** The description is the scope gate. Give agents fresh or minimal context and exclusive write scopes; the primary owns integration and final proof.

**Core principle:** One focused agent per proven independent domain, run concurrently.

## Independence Gate

Dispatch only when every workstream has:

- a substantial, distinct deliverable and acceptance boundary;
- all needed inputs and tool access;
- no load-bearing dependency on another result;
- exclusive mutable ownership; any shared runtime resource has no material coupling through state, capacity, locks, limits, or failure domain;
- wall-time savings that exceed dispatch and integration cost.

For failures, prove separate causes or failure domains. Different files, tests, symptoms, or labels do not prove independence. Search for shared causes, contracts, fixtures, generated artifacts, state, and assumptions. Keep unresolved coupling with the primary for bounded scoping.

Do not use for tasks inside one accepted plan controlled by `superpowers-subagent-driven-development-psilon`, tightly coupled work, full-system reasoning, exploratory debugging, shared-path reconnaissance, related failures, or overlapping state. Parallel dispatch may cover separate external workstreams outside an SDD plan.

## Dispatch Contract

Give each agent only task-local context:

- **Scope:** one subsystem or deliverable; exact exclusive write paths
- **Target:** users, data, environment, contract, or artifact covered
- **Inputs:** current authoritative sources and verified prerequisites; label unknowns without supplying an expected conclusion
- **Outcome:** exact deliverable and acceptance boundary
- **Constraints:** read/write scope, dependencies, and forbidden effects
- **Return:** artifact or concise evidence report for integration

Use `fork_turns: "none"` or the smallest useful fork. Use full history only when the task truly depends on it.

Dispatch all independent agents in one response; separate responses make them sequential.

## Neutral Failure Prompt

```markdown
Fix these observed failures in src/agents/agent-tool-abort.test.ts:

1. partial output lacks "interrupted at"
2. fast tool is aborted instead of completed
3. pendingToolCount expects 3 results but gets 0

The cause is unknown. Read the tests and implementation, establish the
evidence-backed root cause, and make the smallest supported correction. Change
behavior or expectations only after proving which contract is wrong.

Return the cause, evidence, changes, and focused verification.
```

Never pre-label a cause or prescribe candidate fixes. “Fix all tests,” “fix the race,” and unconstrained “fix it” prompts are too broad or anchored.

## Integrate

When agents return:

1. read each result and inspect its work;
2. verify scopes, assumptions, and changes still do not conflict;
3. integrate under primary ownership;
4. run one outcome-proportionate check after the last integration change;
5. spot-check for systematic agent error.

Agent summaries are claims, not proof. If results reveal coupling, stop parallel mutation and reintegrate the work as one domain.
