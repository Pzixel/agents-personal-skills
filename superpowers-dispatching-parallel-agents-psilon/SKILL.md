---
name: superpowers-dispatching-parallel-agents-psilon
description: Use after bounded scoping proves 2+ substantial workstreams have distinct deliverables, no sequential dependency, non-overlapping mutable state, and parallel work beats overhead. Skip SDD-plan tasks, coupled decisions, shared-path reconnaissance, related failures that may share a cause, trivial splits, overlapping writes, and automatic review. The primary owns integration; never infer independence from files, symptoms, labels, or free slots.
---

# Dispatching Parallel Agents

Dispatch one agent per workstream only when every workstream has a distinct deliverable, no sequential dependency on another result, and writes that overlap no other workstream's files or state. Different files, tests, or symptoms do not prove independent causes; related failures stay with one agent. Coupling through shared capacity, locks, or limits is a dependency; when results reveal coupling, reintegrate.

Brief each agent with its inputs, outcome, write scope, and return shape, without a presumed cause. Dispatch all of them in one response.

The primary owns integration: inspect each result, check that scopes did not collide, integrate, and run one check after the last change.
