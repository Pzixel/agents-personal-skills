# Superpowers psilon provenance and adaptation ledger

## Pinned baseline

- Upstream: <https://github.com/obra/superpowers>
- Release: `v6.2.0`
- Annotated tag object: `0e5cc50e782429b95f933e46443898435b8b37a8`
- Source commit: `3dcbd5c4b48e02263fbf4a3c01e3fe4f81d584d9`
- Fork date: 2026-08-04
- License: MIT; see `LICENSE.superpowers`

Eight directories in this repository are provenance-named personal forks, not
upstream plugin installations. The original plugin remains disabled. Each fork
keeps `superpowers-<upstream-name>-psilon` as both its directory and frontmatter
name so users can identify its lineage. The current set also contains the repository-authored `dragonfly-scripting`
and `pareto-design-selection` skills and the adapted `test-audit` and `retro` skills. The retired ClickHouse guard described below is historical and was not
derived from an upstream Superpowers skill.

## Codex simplify export: 2026-10-13

`codex-simplify` copies the body of Claude Code's built-in `simplify` skill
verbatim, as loaded through the Skill tool on 2026-10-13 (Claude Code
built-in; no source file or version pin is available). The body is not
adapted: Codex reads "the Agent tool" as its own subagent mechanism. Only the
frontmatter is new: the name, and a description that makes any `/simplify` in
a request an unconditional trigger, so Codex follows rules that tell it to run
`/simplify`. It is installed for Codex only.

## Retro adaptation: 2026-10-12

`retro` adapts `skills/engineering/retro/SKILL.md` from
<https://github.com/mattpocock/skills> (MIT; attribution in `retro/LICENSE`).
The owner supplied a Russian draft adaptation on 2026-10-09; no commit SHA of
the upstream file was pinned. Kept: after a session, look for evidence-backed
improvements to the agent and its environment. Removed: the dependency on a
`writing-for-agents` skill and the assumption of separate implementation and
review stages. Added by the draft: period scope by event time, partial-history
disclosure, counting shared fork prefixes once, verifiable pointers, causal
caution, a read-only contract, and a valid zero-change result. Alternatives
reviewed and not adopted: `cursor/plugins` `pstack/skills/reflect`
(multi-agent process, external tracker) and `BayramAnnakov/claude-reflect`
(automatic hooks, broad memory system).

The 2026-10-12 revision translates the skill to English and fixes gaps found
in review: default history locations for Claude Code and Codex, subagent and
fork transcripts, `${CLAUDE_SESSION_ID}` for the current session, bounded
metadata-first extraction instead of reading whole transcripts, a list of
high-signal events, a check of loaded instructions before proposing a rule,
general properties instead of case lists, and a change format that names the
owning file and exact wording. Manual-only invocation is kept in both clients;
`context: fork` stays unused because a forked context cannot see the current
conversation. The draft's fixture validator and its generated results were
dropped; the 20 eval cases and judge protocol moved to `retro-workbench/`.

## Pareto Design Selection: 2026-10-01

`pareto-design-selection` is repository-authored with no upstream. It records
the owner's method for choosing a decision strategy by simulation: isolate the
pure decision core, read-only production probe with a source for every number,
a strategy space that always contains the baseline and the cheap extension,
a calibrated pure simulation over measured inputs, one strategy-by-metric
table with a Pareto set and a 20% tie threshold, owner choice, then an
independently reviewed design, read-only production cost measurement, and
prediction-versus-reality follow-up. Executors are named by role with one
note for the Claude Code orch plugin. No examples or templates are shipped;
the skill carries the rules only.

The owner's 2026-10-01 revision replaces an exhaustive up-front comparison
with staged read-only discovery, necessary-condition and optimistic-bound
screening, and iterative selection, mutation, and combination of promising
families. A failed simplification cannot reject a mechanism it omits; small
components remain eligible for useful combinations. Search defaults to five
rounds, three new candidates per round, and three active family representatives;
two informative rounds without material Pareto progress stop it earlier. Progress
uses a 5% search tie threshold against the frontier at the last progress checkpoint,
allowing small gains to accumulate without crediting already-available advantages.
Search percentages use the positive reference value; threshold equality is material.
Controls remain available, limits do not reset for retries or model changes, and unsafe
unbounded operations need an explicit ceiling. Each investigation batch declares
its work; failed batches and additional repair or observation batches consume
rounds. Final validation has a separate bounded reserve: one batch and at most
one corrective rerun, without new or changed strategy rules. Absolute thresholds
come from the goal or measured scale; otherwise the owner supplies them.
Fidelity grows only when it can change the choice; final recommendations still
require calibrated validation, sensitivity checks, a complete evidence table,
and owner choice. The cheap extension must be resolved before a recommendation;
bounded exhaustion can instead report it as unresolved with an explicit gap. Exhaustion
reports incomplete evidence or the best validated set found, never a global
optimum. Discovery metadata now describes this bounded search.

## Test Audit adaptation: 2026-09-30

`test-audit` comes from commit https://github.com/openclaw/openclaw/commit/80930af448ebabc84174146b56bc106d37fab3b4.

- Preserved the three modes, four authoring questions, all junk-pattern entries,
  candidate evidence fields, and all eight campaign stages, including keeper
  plans, independent preservation review, restored-contract mutations, bug
  controls, and upstream reconciliation.
- Replaced source-repository paths, branded tools, and language/framework-specific
  details with current repository commands and policies. Parameterized tests
  remain one declaration unless rows need different audit decisions.
- Incorporated the relevant test-value requirements directly: both gates
  are mandatory, and configuration spelling, dependency/compiler guarantees,
  source greps, and mechanical forwarding gain no retention exception.
- Preserved qualifying semantic mappings, exact external representations,
  interaction/lifetime contracts, and reachable persisted data. The skill states
  test-body, fake/support, and architectural boundaries without loading policy
  documents.
- The behavioral eval contains 20 synthetic, self-contained scenarios with a
  separate rubric in `test-audit-workbench/`. No private policy documents are
  distributed. See `test-audit-workbench/EVAL.md` for replay.

## Current adaptation: 2026-09-29

The user approved a selective refresh for capable models and installation in
Codex and Claude Code. This section supersedes conflicting baseline workflow
or metadata descriptions below; dated validation records remain historical.
The original derivation stays pinned to v6.2.0. Selected changes were compared
against upstream v6.4.2, commit `8ca22dba9a94f28898bbce59f2537ff4d87c747d`.

- SDD is explicitly selected by the user, not automatically selected by plan
  size. Codex metadata disables implicit invocation; the documented Claude
  installation applies its `user-invocable-only` override. Other skills retain
  their scoped automatic activation. Writing-plans defaults to inline execution.
- Upstream's no-subagents contract is carried into implementer and reviewer
  prompts. The controller owns dispatches, preventing duplicate review trees.
- Reviewers re-read truncated evidence at its source and check revision/claim
  coverage before requesting new execution. Missing evidence remains a gap.
- Commit review ranges reject equal or non-descendant boundaries before any
  package/workspace write. This adapts upstream's guard to preserve local tree
  snapshots, which have no commit ancestry; annotated commit refs are resolved
  consistently for guards and commit IDs.
- SDD rejects unsupported findings with recorded evidence before fix dispatch,
  rather than waiting for five rounds. Repeated failures without new evidence
  require reassessment; stronger-model escalation is conditional on availability.
  Real acceptance violations remain incomplete and cannot be waived by a ruling.
- Brainstorming can run authorized bounded feasibility discovery or hand off a
  Discovery milestone. Conditional approaches remain inadmissible production
  designs until their prerequisites are proved; prototypes are not deployments.
- Writing-plans sizes steps by checkable outcomes and checks proportion without
  imposing a document-length quota. Local evidence-qualified detail, rolling
  horizon, and the optional plan reviewer are retained.
- Debugging chooses observations by unresolved uncertainty instead of requiring
  every phase or reading every reference in full. Causal proof and authoritative
  invariants still govern corrections. Pressure scenarios follow this behavior;
  the obsolete phase-recitation exercise is removed.
- Receiving-review and verification remain self-contained compact skills for
  both clients. Technical verification, authority, proof boundaries, and test
  admission survive; repeated rhetoric and communication micromanagement do not.
  Temporary tests obey the same exclusions as permanent ones. Review handoffs
  preserve governing architecture, test, authority, and delivery requirements.
- The current set contains eight Superpowers forks and `dragonfly-scripting`.
  The former ClickHouse guard was removed on 2026-09-16 in favor of
  `ClickHouse/agent-skills`; its earlier provenance below is historical.

Not imported: universal design/plan reapproval, reviewer-invented requirements,
automatic acceptance of unresolved defects through controller rulings, the
seven-analyst diagnosing workflow, or packaging changes unsupported by the
current Git/symlink installation. No claim of measured Astra cost improvement
is made by this refresh.

## Preservation rule

Upstream v6.2.0 is the default authority for bodies, prompts, scripts, examples,
and process knowledge. A local deviation requires at least one of:

1. A direct conflict with higher-priority Codex, user, or repository policy.
2. An upstream dependency or tool that is unavailable in the retained skill set.
3. A measured harmful execution trace.
4. A paired evaluation showing a safer or more efficient result without losing
   the skill's intended capability.

Prefer narrower frontmatter activation, conditional wording, progressive
disclosure, or a native Codex equivalent over deletion. Syntax validation alone
does not prove trigger accuracy or capability preservation.

## Evidence used for adaptations

- The user-provided trivial-filter trace took about 46 minutes and 400,177 goal
  tokens after a one-line `1h` option activated process cascades. This supports
  excluding clear local changes from brainstorming, planning, delegation,
  automatic review, and high-risk verification.
- A 2026-08-05 Contact reply-rate trace produced seven documentation-only
  commits before implementation. Its first complete plan promoted a
  selection-dominated 30-day boundary that campaign-ground-truth analysis
  rejected 24 minutes later. After a replacement implementation plan was
  committed, 123 plan-line and 89 spec-line additions/deletions replaced a new
  association generation, hashed identity, standalone crate, snapshot
  reconstruction, and progress ordering before code began. The corrected next
  milestone then named all 16 paths changed by the first implementation commit.
  This supports evidence-qualified detail and a rolling horizon rather than
  either no planning or exhaustive speculative planning.
- The 2026-08-10 investigation in a Codex task found that an agent proved the MIME
  parser supported a custom tracking header but recommended it for one
  customer workspace despite a current README statement that the workspace had
  no such campaign ledger.
  The agent repeated the recommendation, and its reviewer was anchored by a
  prompt that presented metadata reachability as part of the candidate design.
  This is direct evidence for a target-scope prerequisite gate: capability does
  not prove coverage or applicability, and reviewers must receive authoritative
  sources without receiving the proposed conclusion as fact.
- The user's operating policy requires autonomous resolution of ordinary
  reversible engineering choices, with questions reserved for consequential
  intent or authority boundaries. This supports conditionalizing mandatory
  approval and execution-menu gates.
- The active repository policy explicitly forbids worktrees, authorizes direct
  work on the current branch, admits tests only when they protect required
  observable behavior, and requires outcome-proportionate fresh evidence. This
  supports deferring workspace, branch, test, and verification defaults to the
  governing repository.
- Codex skill metadata is the implicit activation surface; the body loads after
  activation. This supports changing descriptions first and retaining method
  content after the narrower gate.
- Codex collaboration inherits the parent model and reasoning effort by
  default. Model overrides should be exceptional and use supported runtime
  names, so upstream's mandatory generic tier override was conditionalized.
- Retained skills do not include `using-git-worktrees`, `executing-plans`,
  `finishing-a-development-branch`, or `test-driven-development`. References
  that made those unavailable skills mandatory were redirected or made
  conditional.

## Global metadata deviations

At the metadata layer, every fork changes these fields; the body and resource
deviations are listed separately below:

- `name` becomes the provenance-preserving `-psilon` name.
- `description` becomes a positive trigger plus explicit negative boundaries.
- `agents/openai.yaml` keeps `policy.allow_implicit_invocation: true` so hard
  tasks activate automatically; users do not need to opt in.

Bodies retain the upstream method except for the clause-level deviations below.
Most repeat a short “Codex 5.6 adaptation” scope note where upstream language
would otherwise broaden the frontmatter trigger. Writing-plans additionally
uses a measured harmful trace to restructure detail admission and plan form
while preserving durable handoff, sequencing, validation, and recovery.

On 2026-08-10, the eight bodies were compressed for lower context cost. The
edit removed repeated flowcharts, examples, warnings, and restatements; it did
not remove trigger boundaries, hard gates, authority rules, recovery paths,
force, exceptions, or review ordering. The validation record below gives the
measured reduction and independent checks.

## Clause-level deviations

### `superpowers-clickhouse-table-design-psilon`

Added 2026-08-13 from a user request that every agent read the supplied 2026
ClickHouse query-optimization guide before creating or modifying a ClickHouse
table. The supplied guide is preserved as a bundled reference. The skill makes
a complete fresh read its first fail-closed step, activates for proposed and
actual table-definition changes, and excludes query-only or data-only work.

The workflow treats the guide as required input while requiring exact-target
evidence for schema decisions, so general optimization advice cannot override
repository authority, data semantics, version compatibility, or measured
workload behavior.

### `superpowers-brainstorming-psilon`

Retained: project exploration, scope decomposition, one-question discipline when
a question is necessary, approach comparison, YAGNI, design presentation,
isolation and file-boundary guidance, existing-codebase guidance, spec
self-review, visual companion, its reviewer prompt, and all five companion
scripts.

| Upstream clause | Evidence | Ppsilon adaptation |
|---|---|---|
| Use before every creative change, including config and single-function work | Trivial-filter trace; metadata is Codex's activation gate | Trigger only for consequential unresolved design or coupled decomposition; clear local work is excluded |
| No implementation until user approval for every project | Autonomous operating policy | Keep the no-implementation-before-design gate, but require external approval only for consequential user-owned or authority decisions |
| Mandatory nine-item checklist and user approval after each design section | Trivial-filter trace; autonomous operating policy | Preserve the sequence as applicable tasks; investigate and infer first, then ask only consequential questions |
| Always propose 2-3 approaches | No value when only one viable approach survives evidence | Compare 2-3 materially viable approaches; explain when only one is credible |
| “Viable” approach selection and spec review checked internal coherence but not target applicability | 2026-08-10 tracking-header trace and anchored review | Before comparison, verify every load-bearing prerequisite for the exact target, search for disconfirming evidence, reject false candidates, and keep unknown candidates conditional; when no current candidate passes, report the block and do not label a future option as the recommended design; give the reviewer target sources without asserting the conclusion |
| A direct plan request could bypass brainstorming despite unresolved architecture | Paired Sol High hard-task run selected writing-plans alone | Explicitly activate brainstorming before planning only when consequential product or architecture choices remain unresolved |
| Always write and commit a design spec | Trace and repository artifact/commit policy | Persist a spec only when complexity, handoff, approval, or repository policy gives it durable value; commit only when authorized |
| Mandatory second user review of the written spec | Autonomous operating policy | External review is conditional on an approval boundary, explicit checkpoint, or consequential product decision |
| Writing-plans is the only terminal state | Retained set, proportional execution, and target-admission gate | Invoke `superpowers-writing-plans-psilon` only when an admissible design exists and its hard-task trigger matches; otherwise implement directly or report the evidence block |
| Plugin-root path to the visual guide | Fork is a standalone skill directory | Use the relative `visual-companion.md` path |

`visual-companion.md` adds one path-resolution note; all companion code and
the rest of the guide are unchanged from upstream.

### `superpowers-dispatching-parallel-agents-psilon`

Retained: independence analysis, domain grouping, focused prompt structure,
concurrent dispatch, failure examples, integration review, and spot checks.

| Upstream clause | Evidence | Ppsilon adaptation |
|---|---|---|
| Two or three independent items are sufficient regardless of size | Delegation overhead must be outweighed | Require at least two substantial independent deliverables and material wall-time benefit |
| Agents should never inherit session history | Codex supports bounded turn forks rather than a single inheritance mode | Prefer `fork_turns: "none"` or the smallest useful fork; allow full history only when genuinely required |
| “No shared state” framed only as investigation state | Codex agents share a filesystem | Require non-overlapping mutable scopes and primary-agent integration ownership |
| Different files or symptoms can be treated as independent domains | 2026-08-10 capability-to-applicability failure class | Establish causal independence, required inputs, tool access, and exclusive ownership; unresolved shared causes remain with the primary for bounded scoping |
| Body examples and trigger language describe only parallel failure investigation | Cross-file trigger audit | Preserve failure examples but generalize the method to any substantial independent implementation, research, or investigation deliverables |
| Parallel trigger also matched tasks inside one accepted SDD plan | Two of three Sol High activation trials selected both process skills | Exclude tasks already governed by `superpowers-subagent-driven-development-psilon`; parallel owns separate external workstreams |
| Always run the full suite after integration | Outcome-proportionate verification policy | Run the focused or full integrated checks required by the claim's scope |

### `superpowers-receiving-code-review-psilon`

Retained: technical verification, skepticism, feedback ordering, pushback
guidance, examples, and GitHub inline-reply guidance.

| Upstream clause | Evidence | Ppsilon adaptation |
|---|---|---|
| Human-partner feedback is “trusted — implement after understanding” | 2026-08-10 trace; user intent and technical diagnosis have different authority | Treat requirements and authority decisions as binding within scope, but verify diagnoses, factual claims, proposed mechanisms, and their target prerequisites before implementation |
| An unverifiable external claim can be followed after asking whether to proceed | Same | Do not implement a load-bearing unknown as true; investigate available evidence or report the missing external evidence or authority decision |
| Any unclear feedback item stops all work and asks immediately | Autonomous operating policy; cross-file audit found stale ask-first examples | Investigate first, block only the unclear item and its dependents, and continue independently verifiable feedback that cannot constrain the unresolved decision |
| Implement every review item one at a time and test each | Outcome-proportionate verification and test-admission policy | Verify each confirmed fix at its smallest relevant boundary, then run one proportionate regression pass after the last relevant change |

### `superpowers-requesting-code-review-psilon`

Retained: independent read-only reviewer, precise context, requirements and git
range, severity calibration, action on findings, reviewer rubric, output format,
and example findings.

| Upstream clause | Evidence | Ppsilon adaptation |
|---|---|---|
| Review after every task, every major feature, and before every merge | Trivial-filter trace; proportional review policy | Trigger one integrated review at explicit high-risk boundaries or user request; repeat only for a named residual risk |
| Review early, review often | Same | Review at meaningful risk boundaries |
| Default `HEAD~1` base | Drops multi-commit coherent changes | Use the recorded change start or repository-authoritative merge base |
| Reviewer may create a temporary git worktree | Governing repositories may prohibit all worktrees | Use read-only git inspection; a temporary archive/clone is conditional on governing policy |
| Never skip review because work is simple | Narrow trigger already excludes low-risk work | Never skip after the high-risk trigger substantively matches |
| Reviewer starts from the implementation summary and plan, then must acknowledge strengths | 2026-08-10 anchored-review trace | Supply exact target scope, binding requirements, and current sources; label summaries and plans as claims; check prerequisites and disconfirming evidence before code quality; make confirmed strengths optional and later |
| Generic reviewer checklist assumes permanent tests and complete documentation for every change | Test-admission and requested-scope policy | Audit only admitted tests, outcome-proportionate evidence, and documentation required by the contract or change |
| Reviewer can inspect only a live Git range | SDD final review emits an exact file-backed package, including snapshot tree boundaries | Accept an optional verified `[DIFF_FILE]` as the primary diff source, with the Git range as a reported fallback |

The complete upstream review dimensions, severity rubric, read-only inspection,
output contract, and example remain. The prompt also has the worktree-policy and
independent-applicability adaptations above.

### `superpowers-subagent-driven-development-psilon`

Retained: fresh task briefs, per-plan workspace and recovery ledger, implementer
report contract, preflight contradiction scan, role-aware model reasoning,
sequential implementers, task review rubric, five-round bounded fix loop,
scoped re-review, adjudication ledger, final integrated review, all three prompt
templates, all four scripts, and the rationalization and example content now
split into two linked progressive-disclosure companions.

| Upstream clause | Evidence | Ppsilon adaptation |
|---|---|---|
| Activate for any plan with independent tasks | Delegation/review overhead must be material | Require at least three substantial tasks or two independently large tasks with non-overlapping ownership |
| Independent tasks also matched the generic parallel skill | Two of three Sol High activation trials selected both process skills | SDD owns a controlled task sequence inside its plan; parallel dispatch is excluded for those tasks |
| Mandatory worktree and prohibition on main/master | Direct repository-policy conflict | Follow governing workspace and branch policy; never create a prohibited worktree; allow current branch when authorized |
| Use unavailable `executing-plans` outside the session | Retained-set boundary | Route to durable handoff or native execution |
| Ask the human to adjudicate every plan conflict | Autonomous operating policy | Resolve from authoritative requirements/policy when one clearly governs; batch only consequential unresolved choices |
| Always specify a generic model tier | Codex inherits the parent model and supports a bounded model set | Inherit by default; override only for a supported, justified role-specific need |
| Use unavailable requesting-review and branch-finishing skill names | Retained-set boundary | Point final review to the psilon reviewer prompt and follow governing integration/handoff policy |
| Delete scratch workspace with a raw `rm -rf` instruction | Destructive-action policy | Validate the exact workspace path and prefer recoverable deletion; never touch sibling workspaces |
| Implementer always asks or stops on ordinary uncertainty, always commits, and runs a full suite | Autonomous, git, and verification policy | Investigate and resolve ordinary choices autonomously; stop only for consequential unresolved requirements or authority, unavailable load-bearing evidence, material scope conflict, or a genuine evidence impasse; commit only when authorized; run one outcome-proportionate final pass |
| An accepted plan and its “exact values” become the implementer's and reviewer's factual authority | 2026-08-10 capability-to-applicability and reviewer-anchoring trace | Before each dispatch, verify task prerequisites for the exact target; dispatch only Ready work; separate binding requirements from plan assumptions; give implementers and reviewers target scope and authoritative sources |
| Task extractor recognizes only column-zero `Task N`, while the adapted writing skill emits `Milestone N` | 2026-08-10 cross-file adversarial review and smoke test | Accept either heading with CommonMark's zero-to-three-space indentation outside valid equally indented backtick or tilde fenced examples, without changing task content |
| Uncommitted work is permitted but `review-package` diffs only commits | Same | Add stable tree-object boundaries made through a temporary index and exact authorized path list; reject broad pathspecs, nested repositories, and gitlinks the parent tree cannot represent; require one clean plan baseline; carry a cumulative exact plan scope and cumulative snapshot across tasks; reject live HEAD tree drift; stop on out-of-scope changes |
| Per-plan workspace is keyed only by basename and ledger identity uses the caller's path spelling | Same-name, relative/absolute, and symlink plan smoke tests | Key it by sanitized canonical basename plus resolved-path hash; expose that same resolved path for ledger identity |
| Final review names `MERGE_BASE` but commit-mode setup records only an unnamed starting revision | Cross-file adversarial state audit | Record mode and a plan-wide `MERGE_BASE` before Task 1 in both commit and snapshot modes, separately from per-task BASE values |
| Commit-mode packages can omit uncommitted work, absorb dirty paths, or leave out-of-scope mutations outside the commit | Cross-file adversarial boundary audit; porcelain status cannot detect further edits to an already-dirty file | Require a clean initial tree in both modes; in commit mode require whole-tree cleanliness plus an in-scope BASE..HEAD path set before every review and final integration review |
| Implementer and reviewer prompts require tests and TDD evidence even when governing policy admits no permanent test | Test-admission policy; cross-file audit | Require only admitted tests; otherwise carry outcome-proportionate reproduction or verification evidence through reports and re-review |
| Controller routes only spec and quality verdicts after reviewer gains a target-applicability verdict | Cross-file static audit | Require and route all three verdicts through completion and fix-loop gates |
| Applicability failures enter the ordinary five-round code fix loop | 2026-08-10 adversarial state-machine review | Resolve evidence first; reject or redesign a false mechanism and update the task before code changes; keep unknown prerequisites blocked; enter the code loop only after an admissible route exists |
| Task and fix reviewers read implementer reports before inspecting diffs | 2026-08-10 anchored-review trace and adversarial prompt-order audit | Build the target baseline, inspect the diff and derive the actual mechanism first, then read the brief, findings, and reports as second-pass claims |
| Review packages put implementer-authored commit subjects before the raw diff | Same anchoring failure class | Put the stat and raw diff first and retain only objective commit IDs, not subjects, in the package |
| Snapshot tracked-path detection used `grep -q` under `pipefail` | Deleted-directory adversarial review and 5,000-file smoke test | Consume the complete `git ls-files` result in a temporary file before deciding whether a deleted scoped path must be staged |

Prompt model fields are optional instead of mandatory. The three retained
upstream scripts are adapted for Task/Milestone compatibility, canonical-path
workspace isolation, and commit-or-snapshot review boundaries. The new
`worktree-snapshot` helper provides the cumulative exact-scope snapshot
boundary. All four retain executable mode. The two linked Markdown companions
move the workflow examples out of the main activation body to keep it under the
skill-creator context guideline. The extraction adds a scope disclaimer and
aligns the example with the reviewer output order and labels; it adds no workflow
requirements.

### `superpowers-systematic-debugging-psilon`

Retained: root-cause iron law, four phases, error reading, reproduction,
change analysis, boundary evidence, data-flow tracing, working-reference
comparison, single hypotheses, minimal experiments, fix-at-source discipline,
failed-fix architecture reassessment, rationalization checks, root-cause
tracing, condition-based waiting, polluter script, and pressure examples.

| Upstream clause | Evidence | Ppsilon adaptation |
|---|---|---|
| Activate for every bug, build failure, or obvious mistake | Trivial-work proportionality requirement | Trigger when root cause is uncertain; use bounded confirm-fix-verify for an established direct cause |
| Add diagnostic instrumentation before multi-component fixes | Diagnose requests and production authority may be read-only | Prefer read-only boundary evidence; instrument only when needed and authorized |
| Every fix must add a failing test and invoke universal TDD | Retained set excludes TDD; repository test-admission policy | Require a failing reproduction; add a permanent regression test only when it protects admitted observable behavior |
| Always invoke upstream verification skill | Retained-set boundary and proportional verification | Invoke psilon high-risk verification only when its trigger matches; otherwise use focused verification |
| Discuss architecture with the human after three failures | Autonomous operating policy | Reassess from evidence; ask only for a consequential unresolved product or authority choice |
| Defense-in-depth validates at every layer | Repository validates at independent input boundaries and trusts constructed values | Validate each independently reachable untrusted boundary once; add later containment only for a distinct failure mode |
| A working reference pattern can move directly into a fix, and “95%” of no-root-cause cases are asserted incomplete | 2026-08-10 capability-to-applicability trace; no source supports the percentage | Verify the reference pattern's prerequisites for the exact failing target, keep unknown applicability hypothetical, and describe unknown causes without an invented statistic or automatic retry/timeout prescription |
| Root-cause tracing loops forever when no more evidence or authorized instrumentation exists | Cross-file adversarial state-machine review | Add an evidence-and-authority gate; when exhausted, retain the unknown cause and report the blocked evidence boundary |

`defense-in-depth.md` is conditionalized accordingly. `root-cause-tracing.md`
now routes both tracing dead ends through an evidence-and-authority gate instead
of a symptom fix or an infinite evidence loop. Both post-fix paths add a safeguard only when evidence shows a
distinct independently reachable boundary failure mode; otherwise they proceed
directly to verification. The main skill's quick reference and supporting-technique label use
the same conditional test and boundary rules. The academic and pressure-test
prompts use the fork's actual local skill path. Other debugging resources
remain unchanged.

### `superpowers-verification-before-completion-psilon`

Retained: evidence-before-claims iron law, identify/run/read/verify gate,
claim-to-evidence table, rationalization checks, test/build/requirement/delegate
examples, and explicit reporting of failed evidence.

| Upstream clause | Evidence | Ppsilon adaptation |
|---|---|---|
| Activate before every positive state statement, internal task transition, or delegation | Trivial-filter trace; frontmatter activation model | Trigger for high-risk/broad completion or explicit rigorous verification; low-risk work uses ordinary focused checks |
| Verification must run “in this message” | Codex work spans commentary and tool events | Evidence must be fresh after the last relevant change |
| Always execute the full command and reject partial checks | Claim scope determines sufficient evidence | Run the complete command or observation set for the stated scope; never extrapolate beyond it |
| Regression proof always uses destructive revert red-green | Test-admission and destructive-action policy | Require before/after distinguishing evidence; use revert/mutation only when safe and proportionate |
| Re-run gates before moving or delegating | Repeated unchanged gates add no evidence | Re-run only after relevant change or for a named new risk |
| A command, passing symptom test, or plan checklist can be treated as proof of the broad claim | 2026-08-10 mechanism-capability versus target-coverage trace | Define the exact claim and target, verify load-bearing prerequisites, search for disconfirming or limiting evidence, and require evidence at matching boundaries; a false prerequisite forces an actual-status report; treat plans and prior examples only as evidence indexes |

### `superpowers-writing-plans-psilon`

Retained: durable cross-session handoff, scope decomposition, file
responsibility mapping when verified, explicit cross-task interfaces, global
constraints, exact fragile commands and sequences, executable outcome-level
tasks, self-review, and the optional upstream plan-review capability.

| Upstream clause | Evidence | Ppsilon adaptation |
|---|---|---|
| Activate for any multi-step requirement before code | Trivial-filter trace | Trigger only when a durable plan is justified by stages, cross-component coordination, migration/rollout, handoff, or sequencing risk |
| Writing-plans could activate before unresolved architecture was designed | Paired Sol High hard-task run | Defer to `superpowers-brainstorming-psilon` only for consequential unresolved product or architecture choices, then resume planning |
| Universal TDD, frequent commits, and complete implementation code | Test/commit policy and measured plan bloat | Follow governing policies; include complete code only for fragile exact contracts |
| Every step is a 2-5 minute action | User-provided 46-minute trivial trace and earlier oversized-plan evidence | Use coherent outcome-level tasks; micro-steps only for fragile stateful or safety-critical sequences |
| Every task earns a fresh reviewer gate | Review overhead must be justified | Split at independently ownable, verifiable, or risk-reducing boundaries |
| Map exact files and lock decomposition before task drafting | Contact plan repeatedly replaced association ownership, identity representation, and crate boundaries before code | Name exact files only when inspected and load-bearing; otherwise name boundaries or labeled candidates |
| A complete plan should resolve every implementation detail before execution | Contact plan converted unsettled feasibility and snapshot semantics into exact instructions, then churned before implementation | Use an evidence-backed rolling horizon: detail the next risky milestone and keep gate-dependent later work outcome-level |
| No placeholders or unresolved choices | The absence of an explicit unknown form encouraged plausible-looking guesses | Replace vague placeholders with discovery milestones containing evidence, decision, promotion, stop, and fallback criteria |
| Internal consistency self-review | Multiple revised plans were internally coherent but contradicted later repository and runtime evidence | Add evidence, false-precision, horizon, ownership, and execution-cascade audits |
| Cross-task contract consistency was lost while replacing speculative detail checks | 2026-08-10 adversarial preservation audit | For interfaces frozen in the current horizon, compare producer and consumer names, types, formats, signatures, and ownership; do not freeze provisional later interfaces |
| Verified implementation support can still be generalized beyond its evidence scope | 2026-08-10 tracking-header trace | Add target scope to plan form; verify and try to disprove every selected approach's load-bearing prerequisites; reject false prerequisites and keep unknown ones behind explicit gates |
| Mandatory worktree context | Direct repository-policy conflict | Follow governing workspace policy |
| Mandatory psilon SDD or unavailable executing-plans menu | Retained-set and autonomous-execution policy | Invoke psilon SDD only when its trigger matches; otherwise execute natively without a user menu |

`plan-document-reviewer-prompt.md` remains optional for an explicit or
independently justified high-risk review. Its rubric now checks evidentiary
grounding, false precision, rolling-horizon detail, source ownership, safety,
and restartability; it is not dispatched automatically.

## Skills intentionally not migrated

These upstream skills remain disabled because their literal verdict was
“Disable,” not “retain with a narrower trigger”:

- `using-superpowers`
- `executing-plans`
- `test-driven-development`
- `using-git-worktrees`
- `finishing-a-development-branch`
- `writing-skills`

## Validation record

### 2026-08-10 semantic compression

Against commit `5501303`, the eight `SKILL.md` files changed from 14,420 to
6,464 words and from 1,872 to 706 lines. Frontmatter descriptions are one
physical YAML line, at most 450 characters and 60 words. Each body is at most
250 lines and 2,500 words; repository validation enforces these limits.

- An independent semantic diff found and drove restoration of compressed
  trigger qualifiers, force words, exceptions, recovery loops, and review
  order. Its final pass approved all eight skills with no semantic blocker.
- An independent description-only forward test kept the intended result for
  ten easy, hard, review, delegation, debugging, and completion cases. Its
  final pass approved every trigger boundary and metadata budget.
- The skill-creator validator and the repository validator passed all eight
  skills. Python syntax, shell syntax, local links, YAML, and
  `git diff --check` also passed.

### 2026-08-10 applicability revision

The regression target was the failure in that Codex task: design exact
campaign association for one customer workspace, starting from the proposal to
reuse a custom tracking header and parser support. Evaluation agents had
read-only access to the current `example-service` repository and changed no files.

- A first fresh-context run correctly found the workspace prerequisite false,
  but still titled the future tracking-header mechanism “Recommended design.” This was
  treated as a failed wording test, not a pass. The skill was narrowed to
  require an explicit blocked current status and to keep any future mechanism
  labeled conditional.
- The post-fix fresh-context rerun (no conversation fork,
  high reasoning effort) returned `BLOCKED — no current design is
  implementable`, cited the zero tracking-header rows for that workspace and current README,
  rejected the direct internal identifier and inference, and kept a producer-backed identity stream
  conditional on an authoritative contract and complete target coverage.
- The context-loaded variant independently
  rejected the pure tracking header for that workspace and selected a sending
  provider's identity ledger joined through the exact outbound MIME parent. A follow-up rephrase
  preserved that rejection and the fail-closed boundary.

These outcomes prove the intended behavioral distinction for this scenario:
parser capability did not become evidence of target coverage. They do not prove
the trigger or design quality for unrelated domains.

Structural validation after the final wording and script changes:

- The skill-creator `quick_validate.py` passed all eight skill directories.
- `git diff --check`, Markdown fence balance, and `bash -n` for all four SDD
  scripts passed.
- The SDD smoke repository proved Task/Milestone extraction, collision-free
  workspaces for same-named plans, canonical identity across relative, absolute,
  and symlink plan spellings, commit and snapshot review packages, a cumulative
  two-task final snapshot, indented Task/Milestone headings, valid
  backtick/tilde fence handling, rejection of
  broad pathspecs, and rejection of live HEAD drift.
- Snapshot mode rejects nested Git repositories and gitlinks rather than
  silently representing only their parent gitlink SHA.
- A commit-range package smoke test confirmed stat and raw diff precede commit
  IDs and that the commit subject is absent.
- An out-of-scope untracked secret was absent from both the review package and
  Git object database. A 5,000-file tracked-directory deletion staged through
  `git rm` appeared in full in the snapshot diff, covering both the real-index
  and former `pipefail`/SIGPIPE failure modes. Nested-repository and committed
  gitlink/submodule scopes were both rejected explicitly.
- The hostile external reviewer examined all eight
  skills, changed prompts and scripts, provenance, retained workflows, and
  intentionally unchanged companions. Its final verdict was `Approved. No
  blocking issue remains.`

### 2026-08-04 baseline

Validation was run on 2026-08-04 with Codex CLI `0.146.0-alpha.9.2`, model
`gpt-5.6-sol`, and `model_reasoning_effort="high"`. Behavioral comparisons used
ephemeral temporary repositories; they did not write to production or a user
working tree.

### Structure and resources

- `quick_validate.py`: all eight skill directories valid.
- Pinned-resource comparison: 25 of 25 companion files present, no mode
  mismatches. Eighteen are byte-for-byte upstream copies. The seven modified
  resources are exactly those identified above: the visual-path guide, review
  prompt, three SDD prompts, defense-in-depth guide, and optional plan reviewer.
- Syntax checks passed for all shell scripts and both JavaScript companions.
- A temporary Git smoke test exercised `sdd-workspace`, `task-brief`, and
  `review-package`: the workspace remained ignored, Task 1 extraction stopped
  at Task 2, and the generated package contained the exact one-commit diff.
- Actionable skill bodies and prompts contain no calls to unavailable upstream
  skills, mandatory generic model overrides, or unconditional worktree/TDD
  cascades. Remaining `full suite` and `2-5 minute` text is explicitly
  conditional or illustrative.

### Activation matrix

Three independent description-level Sol High trials covered ten scenarios.

| Scenario | Ppsilon selection |
|---|---|
| Clear local `1h` filter edit | None |
| Unresolved tenant/billing architecture | Brainstorming |
| Accepted multi-stage migration specification | Writing plans |
| Nondeterministic multi-component failure | Systematic debugging |
| Concrete external review feedback | Receiving code review |
| Completed high-risk authentication and migration change | Requesting review and verification |
| Accepted four-task plan with disjoint ownership | SDD only in the final three-trial rerun |
| Distinct substantial failures | Parallel dispatch |
| Obvious typo with direct cause | None |
| Related uncertain failures | Systematic debugging, not parallel dispatch |

The initial accepted-plan case selected both SDD and generic parallel dispatch
in two of three trials. After making ownership explicit, a focused three-trial
rerun selected only SDD every time. This is the measured basis for that trigger
deviation.

### Paired task behavior

Token counts are Codex-reported usage for one representative run per variant;
wall time is approximate because process startup and result polling were
external to the usage record.

| Task | Variant | Activated process | Input | Output | Reasoning | Approx. wall time |
|---|---|---|---:|---:|---:|---:|
| Add `1h` beside `24h`, `7d`, `30d` and run the focused check | Upstream v6.2.0 | Brainstorming and verification; review prompt also inspected | 207,673 | 2,157 | 836 | 100 s |
| Same trivial edit | Ppsilon | None; direct inspect, edit, check, diff | 131,191 | 883 | 159 | 45 s |
| Design and plan tenant isolation plus a 10M-row migration | Upstream v6.2.0 | Brainstorming and writing plans | 53,248 | 3,142 | 1,563 | Not recorded |
| Same hard design and plan, final trigger revision | Ppsilon | Brainstorming then writing plans | 55,849 | 2,429 | 1,146 | Not recorded |
| Diagnose nondeterministic duplicate processing across queue, workers, leases, and PostgreSQL | Upstream v6.2.0 | Systematic debugging | 68,000 | 1,647 | 837 | Not recorded |
| Same uncertain failure | Ppsilon | Systematic debugging | 33,778 | 1,474 | 823 | Not recorded |

Both hard design runs compared isolation alternatives and produced a coherent
migration plan. Both debugging runs identified lease-expiry overlap as the
leading hypothesis, retained credible alternatives, and proposed a correlated
timeline experiment. The psilon result therefore preserves the relevant hard-
task method rather than merely suppressing activation.

No live multiagent implementation was launched solely for evaluation. Static
inspection confirms that a qualifying four-task SDD plan retains at least four
implementer dispatches, four task-review dispatches, and one final integrated
review before any fix rounds. That cost is intentional behind the substantial-
plan trigger and absent from the trivial-edit path.
