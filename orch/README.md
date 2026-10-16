# orch — a measured build-and-review flow for Claude Code

A local Claude Code plugin. For every code change beyond one line, the `/orch:orchestrate`
skill has one Opus session (`orch:builder`, effort `low`) write a short spec, build the change
and run its declared check; two independent Fable reviewers review it, a third Fable reviewer
cross-checks their findings in the tree, the same builder fixes the verified findings (at most
two rounds), and one reviewer rechecks each fix. Hooks enforce the flow's gates (see Files). Why this shape: `evals/REPORT-2026-09.md` and the research
package cited in the skill.

## Nothing private

This repository is public. Nothing from private work repositories is tracked here: no code,
repository names, PR numbers, workspace ids, task names or transcripts. Tracked texts use
generic wording; the specifics live in the ignored `evals/NOTES-private.md` and
`evals/private/`. How changes land is in the repository's [AGENTS.md](../AGENTS.md).

## Using it

1. Type the task (text, or a path to a task file) in a new chat. One chat per task.
2. Wait. The main session only spawns agents and reads their replies.
3. Read the report: result and check, cost, blockers and majors before and after the fix
   round, what no run has verified, and questions for you.
4. Review and merge the branch yourself.

Decisions the builder marks as a risk go to a Fable design review and the flow continues
without asking you; you are asked only for a product question the spec does not settle or a
design BLOCKER that needs an owner choice. A blocker still open at the end is in the report.
Technical questions are answered inside the flow.

## Recommended `settings.json`

```json
{
  "model": "opus",
  "effortLevel": "low"
}
```

## Install (skills-dir plugin)

On macOS 13 or later, run `./install.sh claude` at the repository root ([README.md](../README.md#install-for-one-user)).
It links this directory as `~/.claude/skills/orch` and `~/.claude/{agents,hooks,tools}` to
`orch/{agents,hooks,tools}`.

Claude Code loads it as `orch@skills-dir` on the next session. Check with
`claude plugin details orch` and `claude plugin validate <repo>`. A second account
is installed with `CLAUDE_CONFIG_DIR=~/.claude-accounts/<name> ./install.sh claude`.
Because `~/.claude/{agents,hooks,tools}` are links,
`~/.claude/tools/wait-for` and `~/.claude/hooks/handoff.sh` resolve; every agent is therefore
listed twice (bare and `orch:`), and the gates treat both alike.

## Files

- `.claude-plugin/plugin.json` — manifest; `hooks/hooks.json` is auto-discovered.
- `skills/orchestrate/SKILL.md` — the flow.
- `agents/` — `builder` (spec, code, check, fix rounds), `reviewer` (`mode: review`,
  `crosscheck`, `recheck`; spawned on Fable), `Explore` (code location), `prod-probe`
  (read-only evidence), `watcher` (waits on a rollout or CI run).
- `hooks/` — each file's docstring owns its exact rule; `hooks.json` wires them.
  - `agent-type-gate.sh` — spawn rights.
  - `agent-turn-budget.sh`/`.py`, `handoff.sh`, `handoff-only.py` — per-agent context and cost budget, handoff at the hard stop.
  - `main-budget-gate.py` — main-session budget.
  - `checks-before-done.py` — builder's declared check.
  - `skill-load-gate.py` — keeps large expert skills out of the main session.
  - `no-poll-gate.py`, `sleeping-stop.py` — waiting protocol.
  - `scout-bounds.py`, `bash-output-clamp.py` — output clamping.
  - `offline-gate.py` — offline lock on production access.
  - `secret-vault-gate.py`, `redact-secrets.py` — secret protection.
  - `deny-destructive.py`, `plan-approval.py` — destructive commands and their approval.
  - `merge-gate.py` — landing on a shared branch.
  - `reply-lint.py` — final-reply lint.
  - `task-type-checklist.py` — per-task checklist injection.
  - `worktree-dirty-gate.py`, `worktree-reaper.py` — worktree hygiene.
  - `session-tools-link.py` — `~/.claude/{tools,hooks}` links in a fresh HOME (eval runs).
  - `memory-routing.py` — memory routing reminder; not wired in `hooks.json`.
- `tools/` — `gate_policy.py` (pure gate decisions), `orchestrate-params.json` (thresholds,
  spawn rights, build settings), `check-run`, `wait-for`, `secretctl` with `vault_policy.py`
  and `vault_files.py`, `scout.py`, `scout_policy.py`, `scout-mcp-empty.json`, `clamp.py`,
  `worktree_policy.py`, `tests/`.
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
