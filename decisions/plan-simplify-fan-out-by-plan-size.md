# Plan /simplify fan-out runs from the main session, gated by plan size

## Decision

- `/simplify` on a plan is run by the main session. A subagent asked to write a plan returns it and asks the main session to fan out the lenses; it never runs them itself.
- `orch/hooks/plan-simplify-gate.py` (ExitPlanMode) keeps the old rule for every plan: a `<plan>.simplify.md` written at or after the plan's creation. A plan of at least `plan_simplify.fan_out_min_words` words also needs at least `plan_simplify.fan_out_min_agents` lens agents. Both keys live in `orch/tools/orchestrate-params.json`; the policy is `gate_policy.plan_simplify_decision`.
- The plan is the last `plans/<name>.md` the session wrote (Write/Edit/MultiEdit `file_path`, excluding `-agent-<hex>.md` drafts and `.simplify.md`), read from `transcript_path`. No such write: the gate passes.
- A lens agent is a file `<dirname(transcript_path)>/<session_id>/subagents/agent-<id>.jsonl` whose sibling `agent-<id>.meta.json` has `spawnDepth == 1` and whose first user message contains the plan's file name, at any time. No time filter: measured 2026-10-20 on 3 real plans (sessions 2a7de0df, 38de5e74, 61cccd6e), the harness Write and Edit both replace the plan's inode, so `st_birthtime` equals the last Write or Edit; a time filter dropped every lens agent once the plan was reworked from their findings, which the refusal text prescribes.
- Words are a whitespace split. Threshold 1200, `fan_out_min_agents` 2.

## Why

Observed 2026-10-20 (session 2a7de0df): the main session re-tasked a running `reviewer` with "write the plan, then run /simplify". The spawn gate allows a reviewer to spawn only Explore, so all 16 of its spawn attempts were refused and one agent ran all five lenses alone. The owner kept spawn rights unchanged and asked for the delegation rule in the skills plus a size threshold at ExitPlanMode.

Threshold basis, measured 2026-10-20 over the owner's plan directories: 75 files were 53 plans and 22 agent drafts. Plans only: n=53, median 891 words, p75 1315, max 4187. 1200 sits just under p75: about the top quarter of plans takes a fan-out, smaller plans keep the one-agent pass.

The plan comes from the session transcript because the newest file in the shared plans directory may belong to another session started the same day, or be a `-agent-` draft; gating that file refused the wrong session.

`spawnDepth == 1` because `subagents/` is flat: an Explore spawned by a lens reviewer, naming the plan, would otherwise count as a second reviewer.

## Alternatives rejected

- Let `reviewer` spawn `reviewer` (loosen `spawn_rights`): nested fan-out hides its cost from `main-budget-gate`, and the owner kept spawn rights unchanged.
- Count agents from the `.simplify.md` findings file: self-reported by the agent that wrote it.
- Threshold in tokens: needs a tokenizer; words times about 1.4 gives the same quartile cut.
- Count every subagent spawned after the plan: an unrelated Explore or builder would satisfy it; the file-name match ties an agent to the plan.
- Gate the newest file in `<config dir>/plans` (the previous behaviour): cross-session false refusals, above.

## Known blind spots

- The subagent that wrote the plan counts as a lens agent if it was spawned by the main session and its first message named the plan file. Accepted to keep the check simple; the skill prose forbids that pattern.
- `claude --resume` may change `session_id`, so the earlier subagents directory is not found (unsettled; 0 of 1888 transcripts showed a changed `sessionId`).
- A depth-1 agent whose first message named the plan's slug before this plan existed counts (dropped time filter); no instance in the 3 measured sessions.
- The `.simplify.md` rule compares against `st_birthtime`, which is the last plan rewrite, so findings must be re-saved after the last rewrite (passed in 2a7de0df and 38de5e74 because findings came last; refused in 61cccd6e after a later Edit).
- `"/plans/" in path` also gates repository plan files (`docs/.../plans/`), wider than the old `<config dir>/plans` scope; 0 of 236 real ExitPlanMode calls had a repository plan last.
- The transcript and meta layout belong to the harness (verified on real files 2026-10-20: first line `type: user` with `message.content`; meta has `agentType`, `spawnDepth`). If it changes, the count drops to 0 and large plans are refused until the hook is fixed; the refusal names the count, so the cause is visible.
