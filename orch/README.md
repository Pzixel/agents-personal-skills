# orch — a measured build-and-review flow for Claude Code

A local Claude Code plugin. For every code change beyond one line, the `/orch:orchestrate`
skill has one Opus session (`orch:builder`, effort `low`) write a short spec, build the change
and run its declared check; two independent Fable reviewers review it, a third Fable reviewer
cross-checks their findings in the tree, the same builder fixes the verified findings (at most
two rounds), and one reviewer rechecks each fix. Hooks enforce spawn rights, context and cost budgets, the declared
check, and the waiting protocol. Why this shape: `evals/REPORT-2026-09.md` and the research
package cited in the skill.

## Two CRITICAL rules

1. **This repository never gets a git remote.** No `git remote add`, no push, no `gh repo create`.
   It exists as a local `.git` only.
2. **Nothing from private work repositories is tracked here** — no code, repository names, PR
   numbers, workspace ids, task names or transcripts. Tracked texts use generic wording; the
   specifics live in the ignored `evals/NOTES-private.md`. Before every commit:
   `git grep -nE '<private names>'` on the staged tree must return nothing.

## Using it

1. Type the task (text, or a path to a task file) in a new chat. One chat per task.
2. Wait. The main session only spawns agents and reads their replies.
3. Read the report: result and check, cost, blockers and majors before and after the fix
   round, what no run has verified, and questions for you.
4. Review and merge the branch yourself.

You are asked something only for product questions and for decisions the builder marked as a
risk (after a Fable design review); a blocker still open at the end is listed in the report.
Technical questions are answered inside the flow.

## Recommended `settings.json`

```json
{
  "model": "opus",
  "effortLevel": "low"
}
```

## Install (skills-dir plugin)

On macOS, run `./install.sh` at the repository root ([README.md](../README.md#install-for-one-user)).
It links this directory as `~/.claude/skills/orch` and `~/.claude/{agents,hooks,tools}` to
`orch/{agents,hooks,tools}`.

Claude Code loads it as `orch@skills-dir` on the next session. Check with
`claude plugin details orch` and `claude plugin validate <repo>`. A second account
is installed with `CLAUDE_CONFIG_DIR=~/.claude-accounts/<name> ./install.sh`.
Because `~/.claude/{agents,hooks,tools}` are links,
`~/.claude/tools/wait-for` and `~/.claude/hooks/handoff.sh` resolve; every agent is therefore
listed twice (bare and `orch:`), and the gates treat both alike.

## Files

- `.claude-plugin/plugin.json` — manifest; `hooks/hooks.json` is auto-discovered.
- `skills/orchestrate/SKILL.md` — the flow.
- `agents/` — `builder` (spec, code, check, fix rounds), `reviewer` (`mode: review`,
  `crosscheck`, `recheck`; spawned on Fable), `Explore` (code location), `prod-probe`
  (read-only evidence), `watcher` (waits on a rollout or CI run).
- `hooks/`
  - `agent-type-gate.sh` — which agent may spawn which type.
  - `agent-turn-budget.sh`/`.py` — context and cost reminders and hard stop per agent.
  - `handoff.sh`, `handoff-only.py` — commit and hand off at the hard stop.
  - `checks-before-done.py` — the builder may not finish without its declared check.
  - `skill-load-gate.py` — the builder and reviewers load the expert skills they need.
  - `no-poll-gate.py` — refuses polling waits and background Bash in subagents.
  - `sleeping-stop.py` — returns a subagent that ends its turn to wait.
  - `scout-bounds.py` — clamps Grep/Read output for `Explore`.
  - `offline-gate.py` — blocks production access while an offline lock is held.
  - `main-budget-gate.py` — keeps the main session from doing the work by hand.
  - `secret-vault-gate.py` — refuses any tool call that would read `~/.secrets`, Secret payloads or a known value.
  - `redact-secrets.py` — masks known vault values and key-shaped tokens in Bash/Read/Grep results.
  - `session-tools-link.py` — links `~/.claude/{tools,hooks}` in a fresh HOME (eval runs).
- `tools/` — `gate_policy.py` (pure decisions for every gate), `orchestrate-params.json`
  (thresholds, spawn rights, `build.fix_rounds_max`, `build.reviewers`), `check-run`,
  `wait-for`, `secretctl` + `vault_policy.py`/`vault_files.py` (use secret values without printing them), `scout.py`/`scout_policy.py`, `tests/`.
- `evals/` — the eval suite; the synthetic fixture `evals/fixtures/mini/` is tracked, results
  and `NOTES-private.md` are ignored.

## Tests and evals

```
python3 -m unittest discover -s tools/tests -q
claude plugin validate .
evals/run.sh iterate feature-small      # one run, with the plugin
evals/run.sh tierA                      # tier A and negatives, with and without, 3 runs
```

Details, cost and how to read the results: `evals/README.md`.
`tools/tests/test_usage_audit.py` loads `~/.claude/usage-audit.py`, which is not part of this
repository.
