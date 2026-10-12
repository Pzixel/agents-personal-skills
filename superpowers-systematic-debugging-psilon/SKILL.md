---
name: superpowers-systematic-debugging-psilon
description: Use for an observed technical failure with uncertain root cause, especially recurrence, nondeterminism, concurrency, performance regression, production incident, multi-component failure, or a failed prior fix. Skip feature work, known one-line mistakes, and established direct causes.
---

# Systematic Debugging

> **Scope:** The description is the scope gate. Preserve root-cause-first investigation for uncertainty; use bounded confirm-fix-verify for a known direct cause. Follow governing authority and test-admission policy.

**Core principle:** Establish the required behavior and evidence-backed cause before selecting a corrective change. Confidence or a passing symptom check does not establish either.

## Iron Law

```text
NO FIXES WITHOUT ROOT-CAUSE INVESTIGATION FIRST
```

An untested hypothesis may guide investigation, not be presented as a confirmed fix.

Use this especially under time pressure, after failed fixes, when a “quick fix” looks obvious, or when you do not understand the issue. Do not skip because the symptom looks simple or someone wants speed. For a proved direct mistake, confirm, make the smallest coherent fix, and verify; do not inflate it into this workflow.

## Investigation and Correction

Choose the next observation by the uncertainty it resolves. Use the methods below as needed; do not repeat established evidence or complete a phase merely for ceremony. Before changing behavior or test expectations, establish the authoritative invariant, supported inputs, failing boundary, and causal evidence sufficient for that change.

### Establish the Failure

Before any fix:

1. **Read errors:** inspect full messages, warnings, stacks, paths, lines, and codes.
2. **Reproduce:** record exact steps and consistency. If it will not reproduce, gather data; never guess.
3. **Check change:** inspect diffs, commits, dependencies, configuration, and environment.
4. **Locate the failing boundary:** in multi-component flows, observe inputs, outputs, state, and config at each boundary. Prefer read-only evidence; instrument only when needed and authorized. Use the smallest safe observation that isolates the failing component, then investigate it.
5. **Trace data:** for a deep symptom, trace the bad value and callers backward to its source. Fix the source, not the visible sink. See [root-cause-tracing.md](root-cause-tracing.md).

### Compare a Relevant Pattern

1. Use similar working code when it can distinguish the suspected cause.
2. Read enough of the reference and its callers to understand the relevant contract, ordering, and resource lifetime; expand when those depend on code not yet inspected.
3. Compare differences that could affect the failing behavior. Expand the comparison if the evidence does not distinguish the cause.
4. Map required components, settings, environment, state, and assumptions.
5. For each reference pattern, identify load-bearing prerequisites; verify them for the exact failing target and seek missing dependency, config, state, coverage, or contract. A working example proves capability only. Unknown applicability remains a conditional hypothesis, never a proposed fix.

### Test a Hypothesis

1. State one specific theory: “X is the root cause because Y,” including target scope and verified prerequisites.
2. Test one variable with the smallest possible change; never bundle fixes.
3. If confirmed, continue. If not, form a new hypothesis—do not stack another fix.
4. If unknown, say exactly what is unknown and investigate. Ask only when consequential intent, authority, or unavailable external evidence blocks progress.

### Correct and Verify

1. Capture the simplest independent failing reproduction when feasible. Add a permanent or temporary test only when governing policy admits both the behavior and a plausible defect; otherwise inspect directly and use relevant runtime evidence. Mechanical forwarding does not earn a disposable test.
2. Make one root-cause fix. No “while here” work or bundled refactor.
3. Re-run the reproduction and admitted related checks. Confirm the issue is gone. Use `superpowers-verification-before-completion-psilon` only when its high-risk trigger matches; otherwise verify directly.
4. If it fails, inspect what the result disproves before another attempt. After repeated failure without new evidence, stop retrying and reassess the causal model, prerequisites, and shared assumptions. Resume only with new evidence or a materially changed approach.

Repeated failed fixes suggest—not prove—a bad model or architecture. Look for new coupling, unexpectedly broad changes, or new symptoms. Resolve from requirements and boundary evidence; ask only for an unresolved consequential product or authority choice or unavailable required evidence.

## Stop Signals

Reassess the evidence if you think:

- “quick fix now, investigate later,” “probably X,” or “just try it”;
- “change several things and run tests”;
- “skip the failing reproduction”;
- “I do not understand, but this might work”;
- “I can adapt this without understanding its relevant contract”;
- “one more attempt” after two failures;
- or you list fixes before tracing data.

When the user challenges an assumption or says to stop guessing, inspect that assumption and the missing evidence; do not restart unrelated investigation.

Time pressure does not make confidence causal evidence. Bundled speculative fixes obscure which hypothesis was tested.

## If No Repository Root Cause Exists

Only after boundary evidence excludes supported repository-owned causes may you classify the issue as environmental, timing-based, or external. Document the search; verify that required behavior and authority permit handling; add the smallest applicable retry, timeout, or accurate error only when its exact-target prerequisites hold. Add telemetry only when authorized and able to distinguish remaining modes.

Failure to find an internal cause does not prove an external one. Unknown remains unknown.

## Supporting Techniques

- [root-cause-tracing.md](root-cause-tracing.md) — trace backward to the trigger
- [defense-in-depth.md](defense-in-depth.md) — guard only evidenced independent boundaries after root cause
- [condition-based-waiting.md](condition-based-waiting.md) — replace arbitrary delays with condition polling
