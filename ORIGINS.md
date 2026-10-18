# Provenance and adaptation ledger

Current state of every skill, where it came from, and the evidence behind each change that is still in force. Superseded entries (the 2026-08 clause-level deviation tables, validation records, activation matrices, and the full history of the retired skills) are in git history up to commit `695271f` (2026-10-15).

## Pinned upstream

- Superpowers: <https://github.com/obra/superpowers>, release `v6.2.0`, tag object `0e5cc50e782429b95f933e46443898435b8b37a8`, source commit `3dcbd5c4b48e02263fbf4a3c01e3fe4f81d584d9`, forked 2026-08-04, MIT (`LICENSE.superpowers`). Selected changes were compared against v6.4.2, commit `8ca22dba9a94f28898bbce59f2537ff4d87c747d`, on 2026-09-29.
- Each fork keeps `superpowers-<upstream-name>-psilon` as directory and frontmatter name. Every fork narrows `description` to a positive trigger plus explicit negative boundaries and keeps `agents/openai.yaml` with `policy.allow_implicit_invocation: true`.

## Current set

| Skill | Origin | State |
|---|---|---|
| `superpowers-writing-plans-psilon` | Superpowers fork | Kept after a replay eval (below); body shrunk to its contract 2026-10-14 |
| `superpowers-dispatching-parallel-agents-psilon` | Superpowers fork | Shrunk to the independence test 2026-10-14 (extrapolated) |
| `superpowers-receiving-code-review-psilon` | Superpowers fork | Shrunk to prerequisite checks 2026-10-14 (extrapolated) |
| `superpowers-requesting-code-review-psilon` | Superpowers fork; body of Claude Code's built-in `/code-review` | Export 2026-10-14; verdict sentence changed after a replay eval |
| `superpowers-verification-before-completion-psilon` | Superpowers fork | Unchanged after a replay eval (below) |
| `dragonfly-scripting` | Repository-authored | Shrunk by deletion 2026-10-14; eval 11/11 before and after |
| `test-audit` | openclaw/openclaw commit `80930af448ebabc84174146b56bc106d37fab3b4` | Body replaced by a 209-word stub 2026-10-14 after an 82-run eval |
| `pareto-design-selection` | Repository-authored | Owner's 2026-10-01 revision; shrunk by deletion 2026-10-14 (extrapolated) |
| `retro` | mattpocock/skills `skills/engineering/retro` (MIT, `retro/LICENSE`) | English rewrite 2026-10-12; shrunk by deletion 2026-10-14 (extrapolated) |
| `codex-simplify` | Body of Claude Code's built-in `simplify` skill, loaded 2026-10-13 | Verbatim; only the frontmatter is new |
| `orch` | Repository-authored Claude Code plugin | Design and measurements in `orch/README.md` and `orch/evals/` |

## Method and the rule it produced

Replay eval: a fresh agent answers a scrubbed task reconstructed from a real session; arms NONE (no skill text) and OLD (the shipped body), two or three runs per arm; blind grading by a separate agent against Accept/Reject items taken from the owner's later correction. A body is retired or cut only when NONE is not worse than OLD on every case, or the cut is labelled as extrapolated from such a result.

Across the 2026-10-14 evals the model reached the same decision with and without the body on 17 of 18 cases (verification 8 cases, NONE 7/8 vs OLD 8/8; test-audit 10 cases, 10/10 both). What moved a result was a single sentence stating a decision rule the model gets wrong on its own (3/3 vs 0/3 on one case). Hence the maintenance rule: a body keeps decision rules the model misses, output shapes, named thresholds, and tokens a hook or tool checks; everything else is deleted or moved to a companion file. Clauses that restated the owner's user-level rules were removed from every skill on 2026-10-14 ("global-rule dedupe"); descriptions were left unchanged.

## Always-on rules

`rules/always-on-claude.md` and its `.details.md` companion are Claude Code mechanism text moved on 2026-10-18 from the owner's private Claude Code rules (one private config path made generic), plus one owner rule of 2026-10-18 (a turn that hands control to the owner describes the blocker in full; the owner's answer is asked through AskUserQuestion); they are not measured always-on rules and carry no replay numbers.

`rules/always-on.md` moved from the owner's user-level rules on 2026-10-17, verbatim except where noted. Numbers are fresh-session live runs, passes over runs, from a private case set; case content stays there.

- Progressive-disclosure paragraph, thin-slice bullet, existence-witness bullet (one change): CASE-13 0/3 -> 2/3. The paragraph drops a pointer to a private details file.
- Every-part bullet: CASE-15 1/3 -> 3/3. A dated suffix dropped.
- Readings-with-cost bullet: CASE-5 0/6 -> 3/3.
- Cause-of-a-number sentences (split out of a claims bullet): CASE-14 0/3 -> 2/3.
- Window-basis bullet and tunable-default bullet (one change): CASE-16 0/3 -> 5/6.
- Measured-claim bullet (an owner statement against the agent's own measured conclusion is a claim; a question is not the word for an irreversible step): CASE-18 on a long-session packet 1/3 -> 3/3 with the line in the prompt; controls CASE-4, CASE-7, CASE-12 held, CASE-5 0/3 -> 2/3; the gate-field alternative reached 2/3 and was not taken. Live fresh-session check with the line installed and no variant text in the prompt: 3/3 on the same long-session packet.

Move check 2026-10-17: fresh `claude -p --setting-sources project` sessions from an empty directory, rules appended as a system prompt; arm MOVED = this file plus the remaining user-level rules, arm FULL = the unchanged user-level rules, same flags. Acceptance: MOVED >= FULL per case. Every MOVED and FULL number below is the joint grade (both arms blind in one grader call per case), the only grade both arms share; CASE-5, CASE-4 and CASE-7 ran MOVED only (standalone grade). On the joint grade MOVED >= FULL on every case run:

| case | recorded live passes | MOVED passes | FULL passes |
|---|---|---|---|
| CASE-13 | 2/3 | 2/3 | 1/3 |
| CASE-14 | 2/3 | 0/3 | 0/3 |
| CASE-15 | 3/3 | 2/3 | 2/3 |
| CASE-16 | 5/6 | 5/6 | 5/6 |
| CASE-1 (control) | 4/6 | 0/6 | 0/6 |
| CASE-5 | 3/3 | 3/3 | not run |
| CASE-4 (control) | 3/3 | 3/3 | not run |
| CASE-7 (control) | 3/3 | 3/3 | not run |

Grader instability: the earlier standalone grade of the same MOVED texts gave CASE-13 0/3, CASE-14 1/3, CASE-16 4/6; against it CASE-16 would sit one run below FULL (4/6 vs 5/6). Why both arms fall below the recorded live numbers on CASE-14, CASE-15 and CASE-1 is unchecked.

## Per-skill notes

### Superpowers forks (common)

The 2026-09-29 refresh for capable models: reviewers re-read truncated evidence at its source; writing-plans sizes steps by checkable outcomes with a rolling horizon and no length quota; receiving-review and verification stay compact and self-contained; temporary tests obey the same exclusions as permanent ones. Not imported from upstream: universal design and plan re-approval, reviewer-invented requirements, automatic acceptance of unresolved defects, the seven-analyst diagnosing workflow, and `using-superpowers`, `executing-plans`, `test-driven-development`, `using-git-worktrees`, `finishing-a-development-branch`, `writing-skills`.

### `superpowers-writing-plans-psilon`

Replay eval 2026-10-14, two cases: on the plan-redirect case NONE scored 2/5 and 2/5 Accept items, OLD 4/5 and 3/5, so the body is kept; on the premature-block case all four runs tied at 4/5 with the same Reject violation. Measured use before the pass: 0 Claude loads, 5 Codex sessions.

2026-10-17: description rewritten to fire on plan requests; the plan-shape rules (short approach first, detailed plan after approval, five-angle plan review before code) moved in from the owner's global rules. Headless replay of the plan-redirect case with the skill as a project skill: loads OLD 0/5, NEW 5/5, NEW on a one-step non-plan question 0/3. Joint grade, current global rules with the OLD skill vs global rules minus the moved lines with the NEW skill, 3 runs each: Accept items 9/15 vs 11/15, Form items 4/6 vs 5/6, no Reject violations in either.

### `superpowers-requesting-code-review-psilon`

`code-reviewer.md` copies the body of Claude Code's built-in `/code-review` skill verbatim in its high-effort recipe, as loaded on 2026-10-14 (no source file or version pin exists). A brief header precedes it: target scope, binding requirements, the review target, and the implementer's claimed result labelled as a claim. The only body adaptation names `AGENTS.md` beside `CLAUDE.md`, because Codex reads `AGENTS.md`. `SKILL.md` only says how to dispatch the reviewer and handle its findings.

Verdict handling, 2026-10-14: "Fix confirmed Critical issues at once and Important issues before proceeding; record Minor issues" became "Fix confirmed findings before proceeding". Replay eval on 8 review moments: with the old sentence the public-repository scrub case (three real Minor inconsistencies, irreversible once pushed) failed 0/3 because the agent recorded the minors instead of fixing them. Every wording that spelled out "fix Minor" passed the scrub 4/4 but flipped the stuck-investigation case from requesting the optional review (3/3) to skipping it (0/6); the shipped plain wording passed the scrub 1/3, pruning 2/2, stuck investigation 2/3, and decided like the old text on the other five cases. It misses the not-worse rule by one run and is kept because the old sentence contradicts the owner's rule to fix confirmed violations. Lesson: a sentence about handling the verdict changes whether the agent requests the review at all.

### `superpowers-verification-before-completion-psilon`

Replay eval 2026-10-14, 8 moments, rubric totals 0-32: NONE 26, OLD 27, a lens rewrite 28 that lost on one case; kept unchanged.

### `dragonfly-scripting`

Repository-authored Dragonfly v1.34.0 Lua/EVALSHA skill grounded in a local docker lab and cited sources (`dragonfly-scripting-workbench/`). Shrunk by deletion 2026-10-14; workbench eval 3 (design a new batched operation), replayed twice per arm, met 11 of 11 expected items with the old and the shrunk body. Execution-model prose moved to `references/regime-and-design.md`.

### `test-audit`

From the openclaw commit above: three modes, four authoring questions, junk-pattern entries, campaign stages, with source-repository paths and branded tools replaced by generic commands. On 2026-10-14 the 1385-word body (at `d83326d`) was replaced by a 209-word stub. Evidence: 82 blind-graded runs over the 20 synthetic workbench cases (`test-audit-workbench/benchmark.md`). The stub passed 20/20; no skill text passed 19/20; the only difference was T10 (a read-only audit over a red test exposing a product defect), which no text and the old body failed 0/6 and the stub passed 5/5. The stub restates T10's Accept item, so T10 is not independent of the rubric.
On 2026-10-17 the Tests rules moved verbatim into `test-audit/tests-rules.md`, which the skill loads. PORTABLE replay (headless sessions with no user-level rules, only this skill installed): 18/18 runs PASS on T03, T06, T07, T09, T13 and control T10, k=3, the skill loaded and `tests-rules.md` read in every run; the 2026-10-14 STUB v2 baseline (with user-level rules) passed the same cases.

### `pareto-design-selection`

Repository-authored method for choosing a decision strategy by simulation: isolate the pure decision core, read-only production probe with a source for every number, a strategy space that always holds the baseline and the cheap extension, staged screening by necessary conditions and optimistic bounds, bounded search (five rounds, three candidates per round, three active families, early stop after two rounds without Pareto progress at a 5% tie threshold), calibrated validation with one bounded reserve, a strategy-by-metric table with a 20% tie threshold, owner choice. No examples or templates ship.

2026-10-17, not adopted: moving the long form of the numeric-goal method from the owner's on-demand rules file into this skill as `optimization-method.md`, cited by the skill and the orch Optimization checklist hook. Headless joint-graded replays, current rules vs moved (passes per runs). Hook pointer line added: CASE-9 5/12 vs 4/12, CASE-10 7/12 vs 6/12; controls CASE-12 2/3 vs 3/3, CASE-3 2/3 vs 3/3, CASE-7 3/3 vs 3/3. Pointer wording identical to the current rules, path only differing, fresh 12+12: CASE-9 3/12 vs 5/12, CASE-10 10/12 vs 9/12. MOVED fell below FULL on a target in every round; a trace sample (round 1, CASE-10, 0 of 4 runs read any file) suggests neither arm reads the long form; that the gaps are run and grader noise is an unchecked inference. The acceptance rule held, and the long form stays in the owner's rules.

### `retro`

Adapted from the Russian draft the owner supplied on 2026-10-09; no upstream SHA pinned. Kept: look for evidence-backed improvements to the agent and its environment after a session. Added: period scope by event time, partial-history disclosure, bounded metadata-first extraction, a check of loaded instructions before proposing a rule, a change format naming the owning file and exact wording, a read-only contract, a valid zero-change result. Manual-only in both clients; `context: fork` unused because a forked context cannot see the current conversation. The eval protocol is in `retro-workbench/EVALS.md` and has not been run.

2026-10-17: reshaped after a manual retrospective run by hand over four days (episodes, owner criteria, text decision cases, blind-graded pre-registered comparisons, fresh-session live checks). The skill now runs that pipeline: episodes, private cases in `$RETRO_EVALS_DIR`, baseline, comparison, grader validation on user-labelled free runs, apply and re-measure. Lessons carried from that run: always-on decision questions lowered neighbouring cases in three of eight comparisons; placement at the decision point decided more than wording; curated continuations did not predict free-run grading (graders disagreed on 3 of 7 cases). Method in `retro/reference.md`.

### `orch`

Explore agent model choice, measured 2026-09-18 on 58 held-out questions: Opus with Grep/Read output clamped by the `scout-bounds` hook scored 0.897 accuracy, 6 accepted-wrong, $0.033 per question; Sonnet (0.862) and Haiku (0.828) under the same clamp were cheaper and not significantly worse at n=58. The numbers lived in `orch/agents/Explore.md` until 2026-10-17; they are provenance, not behaviour, so they moved here.

Owner hand-off Stop check, decided 2026-10-18: a small-model judgement inside the `reply-lint` command hook replaced the phrase regex. The regex (verb-first wait/need shapes plus a closing-question rule, 93 lines of patterns) reached 11/20 recall on an independent reviewer draw with 3/20 false blocks and was abandoned: hand-offs come as noun phrases ("your choice"), work conditioned on the owner's act, and indirect questions. A `type: "prompt"` Stop hook was tried first and rejected: Claude Code wraps it as "has the following stopping condition been satisfied?" over the whole transcript, and in 12 real headless sessions verdicts inverted (hand-offs caught 5/6, plain replies blocked 1/6, canned "insufficient evidence" blocks), the second stop looped, and every stop sent the transcript (about $0.2 per stop at 200k tokens). The command hook sends only the final reply to `claude -p --model haiku` (no hooks, plugins, MCP or settings; in a stream-json run with the plugin loaded it emits no hook event, the same run without `disableAllHooks` emits Stop events), passes the second stop without a model call and fails open on any judge error. Measured through the hook script itself (Stop payload on stdin): the three motivating replies blocked 3/3; recall 19/20 and 20/20 in two runs on the reviewer's held-out hand-offs; false blocks 1/20 clear (a tool-permission request stating the exact action) plus 3 borderline (a fallback named if the owner stays silent, an access need inside a plan) on the first 20 of 32 hand-off blocks in a fresh 200-reply sample, not tuned on; wall time per stop serial p50 3.7 s, p90 4.3 s (12 replies), p50 4.3 s, p90 5.5 s at 6 in parallel (200). Real headless sessions with the plugin: a text hand-off was blocked once and its second stop logged `stop_hook_active: true` with no verdict; a plain status passed. Exempt by prompt: owner-only actions the reply states exactly (a permission the hooks forbid, `! kill ...`, `APPROVE <hash>` for a named plan), waits on the agent's own agents or tasks, limitation statements, and idle ticks of an unattended goal; a wait on the owner's answer while background tasks run still blocks. A pure prefilter (`gate_policy.addresses_owner`) calls the judge only on replies that address the owner or condition work on the owner's act: a second-person pronoun, possessive, finite or imperative verb in Russian or English, "owner"/"владел*", idioms such as "по команде", "жду подтверждения", "пока не скажете", "let me know", or any "?". The first version (pronouns, "owner", "?") skipped 70% of 4972 corpus replies but dropped real hand-offs written as a pronoun-less verb or a condition on the owner's word (reviewer found 7, e.g. "Запускать не буду, пока не скажете."); recall was put above skip rate and the net widened. Now 63% of stops skip the model (0.04 s); all 7 reviewer misses, the 20 held-out hand-offs and the 3 targets reach the judge; 20 random skipped replies (seed 20261019, tails read by hand) hold no hand-off; mean added time is 3.65 s x 0.37 = 1.3 s per stop (judge wall time 3.2-4.4 s single, up to 6.7 s under load, 12 s ceiling). Final code on the fresh 200-reply sample: 31 hand-off blocks, first 20 hand-labelled 1 clear false (a re-request of a declined file permission) and 2 borderline (a fallback named if the owner stays silent); called-stop wall time p50 4.2 s, p90 5.6 s at 6 in parallel. Real headless sessions with the widened prefilter: the hand-off blocked once, its second stop passed with no verdict, a plain status passed with no model call. The owner accepted +1-3 s per stop on condition of at most 2/20 false blocks and at least 16/20 recall.

### `codex-simplify`

Verbatim export of Claude Code's built-in `simplify` body for Codex, which reads "the Agent tool" as its own subagent mechanism. The description makes any `/simplify` in a request an unconditional trigger. Installed for Codex only.

## Retired

- `superpowers-clickhouse-table-design-psilon`, 2026-09-16: replaced by `ClickHouse/agent-skills`.
- `superpowers-brainstorming-psilon`, 2026-10-14: replay eval on one case tied 5/5, OLD had one Reject violation; 6 Codex sessions ever; its two main rules live in the owner's user-level rules, its third (verify each load-bearing prerequisite for the exact target) moved into the owner's `/simplify` plan pass as the "premises" angle.
- `superpowers-subagent-driven-development-psilon`, 2026-10-14: no loads in either client across six months of transcripts. `scripts/test_review_package.py`, which only tested its script, went with it.
- `superpowers-systematic-debugging-psilon`, 2026-10-14: replay eval on 8 debugging moments, 6-item rubric: without the skill 47.5 of 48, with it 46 of 48, same diagnosis in all 8 cases. The body restated the owner's user-level incident rule. Its reference files went with it.
