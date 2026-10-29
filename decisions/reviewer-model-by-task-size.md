# Reviewer model by task size: small on Opus, medium one combined Fable design spawn, large unchanged

Decision (2026-10-29): the orchestrate flow picks reviewer spawns and their model by the task's tier, set at step 1 from the `files:` line.

- small: every file in `files:` is presentation (templates, styles, copy) or configuration. No `design: required`, no design review; code review A/B and the cross-check run on `model: "opus"`. A small task whose builder reply carries a non-empty `risks[]` is promoted to medium.
- medium: any file holds a decision, a query, a handler or state. `design: required`; step 2 is one Fable `orch:reviewer` spawn whose prompt starts `/simplify lens pass ... on <notes>/DESIGN.md (full path)` and also asks the contract review, writing `DESIGN.simplify.md` and `review-design.md`. Code review A/B and the cross-check stay Fable.
- large: a medium task over about 400 LOC or more than one subsystem; sliced as before, two Fable step-2 spawns (fan-out per `plan_simplify`), Fable A/B and cross-check.
- At most 2 design-fix rounds per task; a third design BLOCKER goes to the report as open.

Owner's rule (verbatim): "remove Fable on every small thing, but the really serious decisions must be Fable's. The simplify pass on the design of a task that takes more than 4 hours or 500+ lines of pure business logic stays on Fable. Small reviews are combined into one Fable spawn. Small tasks like fixing css on a site can go to Opus entirely."

Why these boundaries: hours are not known before the task; the `files:` line is, so the small/medium test is a property of the listed files. The large threshold reuses the existing 400-LOC slice threshold instead of the owner's 500: one constant serves both rules, and a second one would create a 400-500 band that is sliced yet reviewed as medium.

Driver (measured in the private eval notes, fable-budget TREND-2026-10-29): design/lens Fable reviewer spawns grew from 75 to 200 per week, $129 to $527, after the design gate, the step-2 simplify pass and the plan fan-out landed (eef833c, 338662d, f179239). One session on 2026-10-27 ran the design review to revision 9 (23 Fable spawns); hence the 2-round cap, mirroring the 2 fix rounds of the code stage.

Expected saving: $250-350 per week, assumed: about half of the design/lens pairs become one spawn and small tasks leave Fable entirely. Re-measure for the week after merge: in retro-evals run `tmp/fable-budget/trend.py` then `spawns.py`, compare design/lens spawn count and $ with 200 and $527.

Compared against:
- A hook forcing the model per tier: the spawn carries no machine-readable size, and the main session is the only spawner of reviewers (gate_policy model_override_decision allows it any model). Rejected.
- A second 500-line threshold beside 400: see above. Rejected.
- Moving code review A/B or the cross-check off Fable for medium and large: decided 2026-10-22 on replay recall (reviewers-stay-fable-at-high-effort.md); the shadow cross-check agreed in 13/45 tasks. Not reopened.
- The plan lens pass of superpowers-writing-plans-psilon is out of scope: a plan covers 3+ stages, large by definition, and that skill names no model.

Gate: plan-simplify-gate counts a lens agent by the DESIGN.md full path and the literal `/simplify` in its first message and does not read the model, so the combined spawn passes it; builder designs are capped at 600 words, under the 1200-word fan-out threshold.
