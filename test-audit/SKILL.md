---
name: test-audit
description: "Invoke whenever writing, changing, reviewing, or sweeping tests. Authoring gate for new tests plus audit workflow for low-value, implementation-coupled, or duplicative tests and the test-only production seams they demand."
---

# Test Audit

Three modes, one value bar. Authoring mode gates every new or changed test at
write time. Audit mode runs focused sweeps of tests that re-assert source,
duplicate stronger proof, couple behavior to implementation, or keep test-only
production seams alive. Continue broad audits as separate coherent follow-up
PRs; optimize for confidence, not deletion count. Campaign mode prunes one
whole subsystem's test surface (every test file the area owns);
before starting one, read [CAMPAIGN.md](CAMPAIGN.md).

## Test value gates

Every case, assertion, fixture, and support element must pass both gates:
qualifying required, observable, repository-owned behavior AND a specific
plausible defect. A credible bug or critical contract label does not override
the exclusions below. Audit every added or materially changed element before
retaining it; passing proves execution only.

Test pure policy directly, with configuration, time, randomness, and external
results as explicit values and no hidden I/O. Test narrow workflows only for
nontrivial contractual behavior depending on interactions, atomicity, or
resource/stream lifetimes. Do not test mechanical glue, including forwarding or
dispatch of decided actions, even in disposable mock/smoke scripts. Do not retest
compiler/type/schema/derive/lint/framework/dependency guarantees, constants/config
spelling, trivial arithmetic, or private structure. Unchecked semantic validation,
mappings, and exact representations at real boundaries may qualify. Derive cases
and expectations independently from authoritative contracts/failure models,
never implementation constants or paths sharing its decisions. If none qualifies,
omit tests and report other verification. No coverage/count quotas.

Exact behavior requires exact equality; tolerances need a protocol or measured
precision basis, and one-sided bounds need documented asymmetric risk. Arrange,
act, observe, assert; split independent cases or use tables. Bodies expose action
and observation; helpers hide only irrelevant construction. Further setup or
alternating actions/assertions require contractual progression.

Use small domain fakes with supported absence/conflict/failure outcomes and
contractual call relationships, not generic infrastructure emulation. Fakes cannot
prove atomicity/protocols; check changed nontrivial guarantees at real bindings in
focused isolation. No integration machinery for unchanged dependencies. Support
must be test-only and necessary for a qualifying test; substantial simulation also
requires a high-risk integration contract that cannot be exercised directly.
Never shape production interfaces solely for tests. Keep policy pure, workflows
narrow, and execution mechanical; extraction alone justifies neither tests nor
support. Preserve atomicity, ordering, cancellation, and resource ownership;
process streams per item/bounded batch without moving all reads before writes.
Add no classes, files, or wrappers merely to mirror these responsibilities.

## Authoring gate

Before adding any test, answer four questions; a missing answer means do not
add it yet:

1. What observable behavior, invariant, or independent contract does it protect?
2. What credible regression makes it fail?
3. Why does existing coverage not already catch that failure? Each contract has
   one primary test owner at the smallest meaningful decision boundary; another layer needs its
   own distinct risk, such as a transport or lifecycle failure the owner cannot
   reach. Prefer extending a table-driven case or shared fixture over a
   near-duplicate test; consolidate duplicated setup in the same change.
4. Does it need a production seam (export, flag, wrapper, injection hook) that no
   production caller needs? If yes, move the test to the real boundary instead.

Then check the test against every [junk pattern](#junk-patterns); a match fails
the gate unless the [retention bar](#retention-bar) names the contract it
independently guards AND both test value gates pass. The retention bar provides
no exception to the exclusions above. A test that would break under behavior-preserving
refactoring is asserting implementation, not behavior; rewrite it at the
owning boundary before landing it.

Establish the invariant independently before changing code or expectations.
Bug regression tests must fail on the pre-fix code for the intended reason and
pass after the owner-boundary repair. A regression test that never demonstrably
failed proves the mock, not the fix. One regression at the owner boundary
covers the bug; do not replay the same scenario at every layer it crosses.

### Closing coverage sweep

The gate above only rejects tests; it never asks what is missing. Before
reporting a test change as done, list every clause of the authoritative
specification or contract that has observable behavior: each status code and
error path, each response field named by the contract, each boundary or
threshold, each integration point where the feature adds or omits something,
each mode switch (flag on/off, empty input, provider failure). Next to each
clause name the one test that owns it, or the reason no test qualifies (a
framework or typing guarantee, a default constant, a mechanical forwarding).
A clause with neither gets a test at its owner boundary before the report;
an excluded clause stays listed with its reason. Include this list in the
handoff. A clause whose behavior is described in the contract and produced
by production code is never excluded merely because its owner test needs a
real boundary (a route, a rendered response, a persisted row) instead of a
pure function.

## Junk patterns

The shared checklist for both modes: the authoring gate rejects a new test that
matches one, and audits hunt for existing tests that do.

- assertion-free coverage probes;
- self-comparisons and identity copiers;
- copied fixtures, inventories, manifests, or export lists;
- exact source, import, or string greps;
- private predicate or call-shape tests duplicated at real boundaries;
- duplicate invocations of the same contract;
- provider-local replays of shared helpers;
- tests whose only purpose is preserving test-only exports, globals, or wrappers;
- dead production code whose only callers are tests;
- expected values produced by the helper or renderer under test;
- mocks that implement the asserted behavior, or one identical mock standing in
  for different APIs;
- fixtures that supply the receipt, admission, or callback ordering the owner
  should produce, or persistence asserted against a store the path never writes;
- capability tests that restate declared flags instead of exercising the
  delivery or acknowledgement the flag promises;
- negative controls that pass for an unrelated reason, such as a denial from a
  different guard or a rejection the production path never reaches;
- names or fixtures that promise more than the input exercises, such as a
  "retires the window" test asserting the window was not cleared.

## Value bar

Tests justify their maintenance cost by protecting required observable behavior
or an independently meaningful contract AND catching a specific plausible defect. In an audit, an existing
test that must change for behavior-preserving source reorganization is suspect,
not automatically deletable; the authoring gate still rejects new ones.

Before judging a candidate, read the complete test and production owner, its
entry point, callers, callees, sibling implementations, overlapping tests, CI
routing, and relevant history.
When the test claims dependency-backed behavior, inspect the dependency source
or types directly.

## Discovery

Keep discovery read-only and report evidence before editing. For broad scope,
run parallel discovery lanes when available:

- core and packages;
- integrations and extensions;
- UI, apps, scripts, and tooling;
- a cross-cutting pattern sweep.

Outside campaign mode, prefer a few high-confidence candidates over a large
speculative inventory. Hunt for the [junk patterns](#junk-patterns).

## Retention bar

Keep a test when both test value gates pass and it independently enforces a
public API, SDK, protocol, migration, storage, security, platform, prompt-byte,
generated cross-language, package, release, or architecture contract. These
labels alone do not qualify; configuration spelling and declared defaults
warrant no tests. Also keep qualifying:

- call ordering when order is observable behavior;
- regressions with a credible failure mode;
- unchecked semantic validation, mappings, and exact representations at real
  boundaries with independently derived cases and expectations; use source
  inspection for direct review, not source-grep tests;
- a retained test that fails on the baseline: treat it as a possible product
  bug, reproduce it, and repair the owner rather than deleting it.

Static or slow is not a deletion reason. A test that resembles implementation
may still be the independent contract; prove otherwise before removing it.

## Candidate evidence

Record every field below before editing. A missing field means the candidate is
not ready for deletion:

- exact test name and location;
- what failure it can actually detect;
- non-test callers of the covered production or support seam;
- stronger remaining owner-boundary proof, or why no proof is needed;
- relevant history, authoritative contract, and the reason the test or seam exists;
- production or test-support deletion unlocked;
- risk and the focused validation command.

Include every reachable persisted/deployed lifecycle, even when current writers
no longer create those records; invent no unreachable compatibility.

## Edit shape

Choose one coherent owner-boundary batch. Delete obsolete test-only exports,
globals, wrappers, and dead production paths instead of preserving aliases.
Move retained regressions to their canonical owners. Consolidate repeated
qualifying package assertions at their owner; remove excluded dependency-guarantee
tests without inventing a replacement.

Prefer net-negative production LOC. Do not add replacement tests that restate
the same implementation, and do not convert uncertain candidates into cleanup
to increase deletion counts.

## Validation

Never edit source or tests while a test process is running in the checkout.
Follow repository testing/runtime rules; route heavy proof through its approved
execution environment.

1. Run the smallest qualifying owner and sibling tests with repository commands.
   Policy proof needs no production writes or environment simulator.
2. For removed source greps or plan assertions, run the existing executable
   validator or dry-run that owns the real contract, or review it directly.
   Do not replace excluded tests with disposable mock/smoke scripts.
3. Run targeted formatting, then `git diff --check`, using local command wrappers.
4. Classify changed paths with the repository's existing gate selector or
   dry-run, if any, then run the actual changed gates required by repository policy.
   Move meaningful coverage with extracted logic; remove superseded in-scope
   tests/support without unrelated suite cleanup.
5. Inspect `git diff --numstat`; report production/tooling separately from
   tests and test support.
6. After final audit edits, obtain mandatory independent review.

## Landing and continuation

Commit, push, open a PR, or land only when authorized. Use the repository's
maintenance, review, CI, and release flow. Land one coherent PR at a time;
after landing, refresh from the current upstream and rerun
read-only discovery for the next high-confidence batch.

## Handoff

Report:

- the closing coverage sweep: each contract clause with its owner test or exclusion reason;
- root cause and removed low-value categories;
- production owner simplifications;
- retained false positives and why they remain valuable;
- focused and full proof actually run;
- production versus test LOC;
- PR and merge state;
- named follow-ups.

Remaining delivery needs an executor, exact action, revision/environment, evidence,
and continuation/finish condition; instruct **CONTINUE** when gates remain.
Claim release completion only with current evidence for every agreed gate;
local checks cannot substitute for required publication, live behavior, or observation.
