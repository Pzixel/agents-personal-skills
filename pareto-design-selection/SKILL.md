---
name: pareto-design-selection
description: Use when several reasonable strategies could change how a system decides (ordering, selection, allocation, scheduling, retry, throttling, routing), a wrong choice costs production, clients, or money, the business owner decides, and the decision is a pure function over observed facts. Cheap sound screening, bounded search, calibrated finalist validation, a Pareto table the owner chooses from. Skip when one option is obviously right, reversal is cheap, the design is execution, rendering, or UI, or no pure decision core exists; say so and stop. For a resource goal (units per second, cost per unit, CPU, disk) the "Optimization method" unit-cost table precedes step 0 and finalists are proved by a thin prototype on the real path, never by simulation alone.
---

# Pareto Design Selection

<HARD-GATE>
Isolate the decision core before drafting candidate rules. Calibrate each
model before using it to compare strategy performance. Do not recommend or
implement a strategy before the finalists have production inputs, full
validation, and the table. For a resource goal, no candidate is compared
before the unit-cost table exists.
</HARD-GATE>

## 0. Isolate the decision core

State the decision as one pure function: observed facts in, complete decision
out (what is chosen, in which order, with which identities). Execution, side
effects, rendering, and persistence stay outside. If no such function can be
isolated, report that the skill does not apply and stop.

## 1. Goal and metrics

- Restate the goal as a checkable property; record separately the constraints
  that may not move.
- Derive 3 to 6 metrics; exactly one is primary. The others capture side
  effects the choice can worsen.
- Resource goal: the primary metric is units of work per second at the
  boundary the customer observes. First produce the unit-cost table (per
  expensive operation: contract minimum per unit, measured actual per unit,
  gap at target load); the "minimum facts" design built from zero joins the two
  controls. Simulation only screens; the finalist is proved by the thinnest
  prototype on the real path.

## 2. Real parameters

One bounded read-only probe: current primary metric, population shape,
binding constraints. Each unmeasurable number becomes an assumption varied in
the sensitivity run. Rebuild the problem statement from the probe data before
designing.

## 3. Screening and search

- Two controls are mandatory throughout: the current behaviour, and the cheap
  extension of the existing mechanism (configuration, a priority, an index). If
  the extension suffices, build nothing.
- Each strategy is an exact rule over observable facts; "adaptive" without a
  rule is not a strategy.
- Screen with the cheapest valid necessary condition or optimistic bound.
  Reject only if even the optimistic benefit cannot meet the goal, or a
  necessary feasibility condition fails; unverified premises make a rejection
  conditional, not established. A toy model
  that removes a key mechanism cannot refute it: record inconclusive. Rejecting
  a standalone option does not reject it inside a package.
- Keep distinct trade-offs; never reduce selection to one scalar score.
- Before a model compares survivors, the strongest available independent
  analyst checks the screening implications, the model, and production
  feasibility.

### Search limits (defaults unless the owner sets others)

A round is one bounded batch at a declared fidelity, its workload fixed before
it starts.

- At most **5 rounds**; at most **3 new candidates per round** and **3 active
  family representatives**, excluding the controls.
- Stop after **2 consecutive informative rounds without material Pareto
  progress** against a reference frontier from the last progress checkpoint.
  Material means a new feasible non-dominated trade-off that no reference
  member matches or beats on every cared-about metric under a **5%** search
  threshold (difference over the positive reference value; strictly below is a
  tie, equality is material), or one that newly meets the goal or a binding
  constraint. The 5% search threshold is separate from the final tie
  threshold. Check all metrics, including production cost; primary-metric
  stagnation alone is not stagnation. Use a fixed absolute threshold for zero
  or signed baselines; if none is defensible, ask the owner for it.
  Inconclusive rounds cannot establish stagnation but still count.
- A failed batch, retry, repair, or fidelity increase consumes a round; never
  reset the counter.
- Final validation reserve: **one bounded batch plus at most one corrective
  rerun**, reserved before search; it cannot introduce strategies. A further
  failure returns incomplete findings with the precise work or budget still
  needed. Never claim a global optimum.

## 4. Pure simulation

- Pure functions and a fake clock; the strategy is the function from step 0.
  The population shape is a real production export.
- Calibration: the current strategy inside the model reproduces the measured
  primary metric within a tolerance fixed before the run. Otherwise fix the
  model, never the targets; recalibrate after changing fidelity or inputs. A
  baseline fit alone does not validate a model that omits a candidate's key
  mechanism.
- Validate finalists and controls across current load, minimum and maximum
  capacity, a new large participant, and failures, with several common seeds.
- Sensitivity: vary every assumption that came from a gap and record whether
  the choice changes. An assumption that can flip the choice must be measured
  or escalated.

## 5. Table and Pareto set

- One table: strategy by metric, plus a cost-in-production row per strategy
  (code to change, new indexes or storage, operational risk), with controls,
  finalists, evidence fidelity, and uncertainty. No unmeasured cells. Other
  explored directions get rejected, deferred, or inconclusive status with
  evidence.
- The Pareto set holds strategies for which no other is at least as good on
  every metric and strictly better on at least one. Differences below a
  threshold fixed in advance (default **20%**) are ties. Only comparably
  validated results enter.
- For each member, state in plain words what it pays for its advantages.
- Give a separate verdict on the cheap extension. Show the owner the whole
  table, never only the conclusions, with the search limits, stop reason, and
  unexplored or deferred families.

## 6. Business choice

- The owner chooses from the Pareto set. The agent recommends and explains
  but does not decide for the owner.
- Technical questions that appear on the way (priorities between neighbours,
  the exact definition of a metric) are decided by the agent from the data,
  with the reasoning recorded alongside the table.

## 7. From choice to production

- The chosen design gets an independent review before any code.
- Fix the cost threshold in advance. Measure it read-only on production in
  the mode the code really runs (real parameters, connection pool, prepared
  statements); a simplified-mode measurement has been wrong by an order of
  magnitude.
- The PR carries the rationale, the table, the Pareto set, the calibration
  result, the assumptions, and a folder that reproduces the simulation with
  one command.
- Before rollout capture baseline numbers and rollback conditions, and arm
  the observation. After rollout compare production with the prediction; a
  discrepancy is a model error.

## Roles

Probe: a read-only production observer (`orch:prod-probe`). Step 3 analysis
and step 7 review: the strongest available reviewer, independent from the
author (`orch:reviewer`).
