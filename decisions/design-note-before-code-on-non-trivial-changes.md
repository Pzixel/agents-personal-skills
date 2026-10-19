# Design note before code on every non-trivial change (2026-10-19)

Decision: a change that is more than a value, a field or a config line gets a written design note (scope and subsystems, size, the premises it rests on and how each was verified, rejected alternatives) before the first code step. The property lives in `rules/always-on.md` (Scope). The orchestrate flow carries the mechanism: the builder prompt says `design: required`, the builder writes `DESIGN.md` and stops, step 2 (design review) runs whatever `risks[]` says; `orch/agents/builder.md` adds the premises line to `DESIGN.md`. Owner approval is not required; any follow-up after the note passes.

Why: one session (2026-10-19) went from a pasted bug report to a `ship` builder in 2 min 16 s. The builder's SPEC had no `risk:` line, so the only design step in the flow never fired. About 900 lines across 14 files were built, reviewed three times and deployed on a premise taken from a docstring (the downstream service renders the sender name from a stored profile); a 2-minute read of that service showed it does not, and the fix moved to the other repository. Week scan 2026-10-12..19: 22 sessions with a PR of 300+ lines, 10 of them with no DESIGN.md, plan file or plan mode (private retro-evals, CASE-24).

Compared against:
- Keep the `risk:` trigger only (status quo): the builder judges "expensive to reverse" on its own and large changes on unverified premises pass through; rejected by the owner's words (K35, private retro-evals).
- A LOC threshold alone (300+): the size is known only after the code exists; the main thread knows the subsystems and the shape at step 1, so the trigger is the shape of the change, the number stays as the owner's rule of thumb.
- Owner approval of every design: rejected by the owner («план = письменный, без моего утверждения»); the gate is the note, not the approval.

Measurement: private retro-evals CASE-24, two blind rounds (k=3 per arm) gave A 3/3, B 3/3 — the text harness does not reproduce the failure: its prompt asks the subject for a 150–500-word message to the owner, which is itself the missing note. The rule is applied on the owner's requirement, not on a measured lift; the check is the next large task in a live session (a `design: required` line in the builder prompt and a `DESIGN.md` before the first code commit).
