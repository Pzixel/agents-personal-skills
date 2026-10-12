---
name: pareto-design-selection
description: Use when several reasonable strategies could change how a system decides (ordering, selection, allocation, scheduling, retry, throttling, routing), a wrong choice costs production, clients, or money, the business owner decides, and the decision is a pure function over observed facts. Cheap sound screening, bounded search, calibrated finalist validation, a Pareto table the owner chooses from. Skip when one option is obviously right, reversal is cheap, the design is execution, rendering, or UI, or no pure decision core exists; say so and stop. For a resource goal (units per second, cost per unit, CPU, disk) the "Optimization method" unit-cost table precedes step 0 and finalists are proved by a thin prototype on the real path, never by simulation alone.
---

# Pareto Design Selection

Choose a decision strategy by evidence, not by argument. Screen cheaply,
develop promising families, and spend on detailed simulation only when it can
change the choice. The owner decides from a Pareto set built on measured
production facts; the agent supplies the facts, model, table, and recommendation.

<HARD-GATE>
Isolate the decision core before screening or drafting candidate rules.
Early bounds and necessary-condition checks may precede a complete probe or
simulation. Every rejection needs a valid implication for the real mechanism;
an inconclusive shortcut is not a failed strategy. Calibrate each model before
using it to compare strategy performance. Do not recommend or implement a
strategy before the finalists have adequate production inputs, full validation,
and the table. Passing a screen only earns further investigation. For a
resource goal, no candidate is compared before the unit-cost table exists.
</HARD-GATE>

## 0. Isolate the decision core

State the decision as one pure function: observed facts in, complete decision
out. Name every input fact and the full output (what is chosen, in which order,
with which identities). Execution, side effects, rendering, and persistence
stay outside the core; the skill designs how the system decides, not how it
acts or displays.

If no such function can be isolated, the skill does not apply. Report that and
stop; do not simulate an execution path or a UI.

## 1. Goal and success metric

- Restate the owner's goal as a checkable property that a measurement can
  confirm or refute. A vague adjective is not a goal.
- Record separately the constraints that may not move (caps, excluded
  tenants, contractual limits) and the dimensions the owner does not care
  about.
- Derive 3 to 6 metrics from the goal. Exactly one is primary. The others
  capture side effects the choice can worsen: load on external systems,
  waiting time of small participants, evenness, cost.
- Resource goal (throughput, cost per unit, CPU, disk): the primary metric is
  units of work per second at the boundary the customer observes, never a
  metric of one part. Before any family is sketched, produce the unit-cost
  table: one row per expensive operation, with the minimum per unit required
  by the contract, the actual per unit measured on the real system, and the
  gap projected to the target load. The "minimum facts" design built from
  zero joins the two controls in the candidate set. Simulation only screens;
  the finalist is proved by the thinnest prototype on the real path with real
  traffic within hours, then guarantees are added one at a time. Method and
  evidence: the "Optimization method" section of the user rules.

## 2. Real parameters before the model

- Start with existing evidence and one bounded read-only probe: the current
  primary metric, population shape, binding constraints, and the layers or
  bottlenecks each direction can affect. Collect further data only to resolve a
  choice-changing uncertainty or support the next model fidelity. Finalists
  still need capacities, duration distributions, throughput, and failures.
- Every number carries the query or command that produced it. A number that
  cannot be measured is a gap, never a guess. Each gap becomes an explicit
  assumption and is later varied in the sensitivity run. Label measured,
  computed, assumed, and unknown values separately.
- Expect the probe to refute the initial picture. Rebuild the problem
  statement from the data before designing anything; a strategy space built on
  the pre-probe picture is wasted work.

## 3. Cheap screening and strategy families

- Sketch distinct families cheaply; there is no quota of fully simulated
  strategies. Two controls are mandatory and remain available throughout:
  - the current behaviour, as the baseline;
  - the cheap extension of the existing mechanism (configuration, a priority,
    an index). Never dismiss it without data. A final recommendation requires
    proved insufficiency or validated sufficiency; if it suffices the answer
    is to build nothing.
- Cover different principles: queue, round-robin, fair share, recency,
  lottery, hybrid. Keep a small diverse shortlist rather than many variants.
- Each strategy is an exact ordering or selection rule over observable facts.
  "Smart choice" or "adaptive" without a rule is not a strategy.
- Screen with the cheapest valid necessary condition or optimistic bound.
  State its domain, preserved mechanisms, omitted interactions, and implication
  for the real task. Reject only if even the optimistic benefit cannot meet the
  goal, or a necessary feasibility condition fails. A toy model that removes a
  key mechanism cannot refute it; record the result as inconclusive. Unverified
  premises make a rejection conditional, not established.
- Assess combined benefit and dependencies. Small components can form a useful
  package; rejecting a standalone option does not reject its use in a package.
  Do not add overlapping savings blindly.
- Select, mutate, and combine promising rules in bounded rounds. Orthogonal
  switches are explicit parameters, not separate families or a full grid.
  Every offspring is an exact pure rule that retains the fixed constraints.
  Use observed weaknesses to guide mutations; combine complementary mechanisms
  only when their contracts and interactions are compatible. Screen offspring
  cheaply before promotion. Keep distinct Pareto trade-offs; do not reduce
  selection to one scalar score.
- Before using a model to compare survivors, give the strongest available
  independent analyst the screening implications, model, and production
  feasibility: window truncation, query cost, and facts diverging between layers.
  Reuse that analysis; revisit it only for a changed mechanism or new risk.

### Search limits

Record limits before the first round; use these defaults unless the owner sets
others. A round is one bounded investigation batch at a declared fidelity,
followed by selection of the next batch. Fix its observations, model work,
candidates, scenarios, seeds, and horizons before starting.

- At most **5 rounds**, including the initial batch; at most **3 new candidates
  per round** and **3 active family representatives**, excluding the two controls.
  Keep previously non-dominated results in the evidence even when deferred.
- Stop earlier after **2 consecutive informative rounds without material
  Pareto progress**. Keep a reference frontier from the last material-progress
  checkpoint, initially the first comparable batch. Progress means a new feasible
  non-dominated trade-off that no reference member matches or beats on every
  cared-about metric under a **5% search tie threshold**, or newly meets the goal
  or a binding constraint. Update the reference only on material progress so
  small gains can accumulate. Compare complete trade-offs; an inherited advantage
  against an unrelated reference member is insufficient. Check all metrics,
  including production cost; primary-metric stagnation alone is not stagnation.
  The 5% search threshold is separate from the final tie threshold. For search
  percentages, divide the difference by the positive reference-member value;
  differences strictly below the threshold are ties, equality is material.
- Compare like fidelity, scenarios, seeds, and inputs. Fix a meaningful absolute
  threshold for zero baselines, signed values, or metrics where percentages are
  unsuitable, deriving it from the goal or measured scale. If no defensible basis
  exists, ask the owner for that threshold before the affected comparison;
  continue independent cheap work. Differences unresolved by uncertainty are
  inconclusive and cannot establish stagnation, but still consume the round limit.
- Stop when a validated cheap extension suffices, or sound bounds leave no
  direction capable of meeting the goal. Deferment due to shortlist size is not
  rejection. Stop at an owner's time, token, or money ceiling if reached sooner.
- A failed batch consumes a round even if no candidate was evaluated. Additional
  observations, retries, model repairs, fidelity increases, and new families
  beyond a batch's declared work start the next round; never reset the counter
  or hide additional attempts inside it. Reserve final-validation effort before
  spending on search.
  If a probe or run cannot be safely bounded, obtain an explicit work/cost ceiling
  before that operation; continue independent cheap work meanwhile.

Stopping ends exploration, not validation. The separate final-validation reserve
defaults to **one bounded batch plus at most one corrective rerun**, with workload
fixed in advance and within any owner ceiling. It may collect needed facts and
repair the model for retained rules, but cannot introduce or modify strategies.
Validate retained finalists before recommending them; a further failure returns
incomplete findings, not another automatic retry or search restart.
Report the stop reason, remaining gaps, and the best validated set found within
the limits, without claiming a global optimum. If adequate final validation does
not fit, return incomplete findings and the precise work or budget still needed.

## 4. Pure simulation

- The model is pure functions and a fake clock. No network, no database, no
  wall time. The strategy is injected as the ordering or selection function
  from step 0.
- Inputs come only from the probe: distributions are sampled from measured
  data, and the population shape is loaded from a real production export, not
  synthesised.
- Use the least detailed model that preserves the mechanism under evaluation;
  increase fidelity only for promising or inconclusive candidates when the
  added detail can change a decision. A baseline fit alone does not validate a
  model that omits a candidate's key mechanism.
- Calibration is mandatory. The current strategy inside the model must
  reproduce the measured production value of the primary metric within a
  tolerance fixed before the run. If it does not, fix the model, never the
  targets. Recalibrate after changing fidelity or inputs. Record rejected model
  variants and their reasons; do not restart the search budget for a new model.
- During search, use the cheapest discriminating scenarios and paired seeds.
  Fully validate finalists and the controls across all relevant scenarios
  (current load, minimum and maximum capacity, a new large participant,
  failures) and several common seeds. A sound bound may already resolve the
  cheap extension. Choose a horizon long enough for slow effects to surface.
- Sensitivity: vary every assumption that came from a gap. Record whether the
  choice changes. An assumption that can flip the choice must be measured or
  escalated, not left as is.

## 5. Table and Pareto set

- Produce one summary table: strategy by metric, plus a cost-in-production
  row for each strategy (code to change, new indexes or storage, operational
  risk). Include the controls and finalists, with evidence fidelity and
  uncertainty. For other explored directions record rejected, deferred, or
  inconclusive status and the evidence; never invent unmeasured cells.
- The Pareto set contains strategies for which no other is at least as good on
  every metric and strictly better on at least one. Differences below a threshold
  fixed in advance (default 20%) count as ties; otherwise noise decides for the
  business. Use metric-appropriate absolute thresholds where percentages are
  unsuitable. Only comparably validated results enter the final Pareto set.
- For each Pareto member, state in plain words what it pays for its
  advantages.
- Give a separate verdict on the cheap extension: why it is insufficient, or
  why it suffices and nothing needs to be built.
- Show the owner the whole table, never only the conclusions.
- State the search limits, stop reason, and unexplored/deferred families.

## 6. Business choice

- The owner chooses from the Pareto set. The agent recommends and explains
  but does not decide for the owner.
- Technical questions that appear on the way (priorities between neighbours,
  the exact definition of a metric) are decided by the agent from the data,
  with the reasoning recorded alongside the table.

## 7. From choice to production

- The design of the chosen strategy receives an independent review before
  any code.
- Fix the cost threshold in advance. Measure it read-only on production in
  the mode the code really runs: real parameters, the real connection pool,
  prepared statements, enough repetitions. A simplified-mode measurement has
  been wrong by an order of magnitude.
- The PR carries the rationale, the table, the Pareto set, the calibration
  result, the assumptions, and a folder with the data and scripts that
  reproduce the simulation with one command.
- Before rollout: capture baseline numbers, define rollback conditions, and
  arm the observation. After rollout: compare production with the
  simulation's prediction. A discrepancy is a model error; find it before
  trusting the next decision.

## Never

- Substitute numbers by eye.
- Compare simulated performance before calibration, or treat a screen as proof
  of final performance.
- Show the owner a recommendation without the table.
- Refine figures that cannot change the choice.
- Recommend a strategy while the cheap extension remains unresolved; an
  exhausted investigation instead reports its unresolved status and evidence gap.
- Invent a strategy that is not an exact rule over observable facts.
- Reject a mechanism using a simplification that removes it.
- Reset limits, hide retries inside a round, or claim optimality at exhaustion.

## Roles

The probe is a read-only production observer; the analysis in step 3 and the
review in step 7 go to the strongest available reviewer model, independent
from the author of the strategies. In Claude Code with the orch plugin these
are `orch:prod-probe` and `orch:reviewer`.
