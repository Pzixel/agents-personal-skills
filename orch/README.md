# orch — a measured build-and-review flow for Claude Code

A local Claude Code plugin. For every code change beyond one line, the `/orch:orchestrate`
skill has one Opus session (`orch:builder`, effort `low`) write a short spec, build the change
and run its declared check; two independent reviewers (Fable; Opus for a small task) review it, a third reviewer
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
It links this directory as `~/.claude/skills/orch` and each entry of `orch/{hooks,tools}`
into `~/.claude/{hooks,tools}`, next to the user's own files, so
`~/.claude/tools/wait-for` and `~/.claude/hooks/handoff.sh` resolve. Agents are not linked:
the plugin lists each once, as `orch:<name>`, and spawns use that name.

Claude Code loads it as `orch@skills-dir` on the next session. Check with
`claude plugin details orch` and `claude plugin validate <repo>`. A second account
is installed with `CLAUDE_CONFIG_DIR=~/.claude-accounts/<name> ./install.sh claude`.

## Files

- `.claude-plugin/plugin.json` — manifest; `hooks/hooks.json` is auto-discovered.
- `skills/orchestrate/SKILL.md` — the flow.
- `agents/` — spawned as `orch:<name>`: `orch:builder` (spec, code, check, fix rounds),
  `orch:reviewer` (`mode: review`, `crosscheck`, `recheck`; spawned on Fable, on Opus for a small task), `orch:scout`
  (code location; named so it does not collide with the built-in `Explore`), `orch:prod-probe`
  (read-only evidence), `orch:watcher` (waits on a rollout or CI run).
- `hooks/` — each file's docstring owns its exact rule; `hooks.json` wires them.
  - `agent-type-gate.sh` — spawn rights.
  - `agent-turn-budget.sh`/`.py`, `handoff.sh`, `handoff-only.py` — per-agent context and cost budget, handoff at the hard stop.
  - `main-budget-gate.py` — main-session budget.
  - `checks-before-done.py` — builder's declared check.
  - `skill-load-gate.py` — keeps large expert skills out of the main session.
  - `no-poll-gate.py`, `sleeping-stop.py` — waiting protocol.
  - `monitor-wait-note.py` — waiting protocol as context on every Monitor call.
  - `scout-bounds.py`, `bash-output-clamp.py` — output clamping.
  - `offline-gate.py` — offline lock on production access.
  - `secret-vault-gate.py`, `redact-secrets.py` — secret protection.
  - `deny-destructive.py`, `plan-approval.py` — destructive commands and their approval.
  - `merge-gate.py` — landing on a shared branch.
  - `reply-lint.py` — final-reply lint.
  - `task-type-checklist.py` — per-task checklist injection.
  - `worktree-dirty-gate.py`, `worktree-reaper.py` — worktree hygiene.
  - `session-tools-link.py` — `~/.claude/{tools,hooks}` links in a fresh HOME (eval runs).
  - `dup-skills-hook.py` — one context line when a skill or agent name is visible from two
    sources (`tools/dup_policy.py`; CLI `tools/dup-skills`), silent otherwise.
  - `memory-routing.py` — memory routing reminder; not wired in `hooks.json`.
- `tools/` — `gate_policy.py` (pure gate decisions), `orchestrate-params.json` (thresholds,
  spawn rights, build settings), `check-run`, `wait-for`, `rm-own` (removes an agent's own
  scratch paths after checking the real path; the destructive gate lets it through), `kill-own`
  (SIGTERMs the agent's own leftover local processes after the ancestry checks; same gate exemption), `secretctl` with `vault_policy.py`
  and `vault_files.py`, `scout.py`, `scout_policy.py`, `scout-mcp-empty.json`, `clamp.py`,
  `worktree_policy.py`, `tests/`.
- `evals/` — the eval suite; the synthetic fixture `evals/fixtures/mini/` is tracked, results
  and `NOTES-private.md` are ignored.

## Hook gates

Hooks enforce the gates; an agent obeys a refusal's message and never works around it.

- Merge: a push or merge to a shared branch carries `CHECK_RAN: <what ran, result>`,
  `PROD_POSITION: <blast-radius dimension, production's number, source>`,
  `WAY_BACK: <undo, minutes>` and `WORSE_FOR: <who gets worse or can break for every
  consumer, the new bottleneck; or none, with the reason>`, or `merge-gate` refuses it.
- Final reply: the Stop hook `reply-lint` returns once a completion claim without its
  observation, a reply that leaves work waiting on the owner's choice, answer or approval
  in plain text (decide it if it is inside the agent's authority, otherwise ask through
  `AskUserQuestion` with self-contained options, written in the language the agent's rules set
  for chat replies), or a deferral of work the agent could do
  this turn that waits on nothing and names no issue, PR or handoff file; it asks for an
  addendum with only the missing part. The hand-off and deferral judgements are one nested
  `claude -p --model haiku` call that sees only the final reply (prompt in
  `gate_policy.JUDGE_PROMPT`), runs with no hooks, plugins, MCP or settings, and runs only
  when the reply addresses the owner (a second-person pronoun or verb, "owner", an idiom
  like "пока не скажете" / "let me know", or a "?") or holds a first-person commitment
  ("поправлю", "I'll fix", "follow-up PR"); about half of stops skip it. Mean added time is
  about 3.4 s per stop (measurement in `ORIGINS.md`), capped by the 12 s judge timeout. The second stop passes
  without a model call. A judge error, timeout or unparsable verdict passes the reply (fail
  open) and is logged in `~/.claude/reply-lint-log.jsonl` as `judge_error`, next to
  `rules` (the rules that fired) and `verdict`; a standing failure (no login, rate limit,
  broken install) fails open on every stop, so a silent check is diagnosed from that field.
- Destructive commands are classified by `deny-destructive.py`
  (`gate_policy.destructive_decision`, table in `orchestrate-params.json`), never by the
  agent. `ours` (paths under `/tmp`, `tmp/`, `.worktrees/`; k8s objects labelled
  `orch.session`; live writes with a cheap mechanical undo on one named object such as `kubectl scale`
  or `kubectl rollout restart|undo`; with `--all`, a selector, several
  objects or `-f`/`-k` they are `approval`)
  runs without a prompt; Flux reconcile/suspend/resume is unclassed and runs. `approval` (a live write whose
  undo is not mechanical: SQL writes, `kubectl apply/delete/patch`) goes into
  one plan script with `# scope / # undo / # count / # contradiction / # prod_position /
  # way_back` headers, run from the main session as
  `bash <abs path>  # DESTRUCTIVE_PLAN sha256=<hash>`; one approval per batch: the owner's
  approval of the plan, the permission prompt, or `APPROVE <hash>` where the session
  cannot prompt. `impossible` (no undo: DROP/TRUNCATE, Secret
  replace, namespace/PVC delete, force push, rm outside `ours`) is refused as a bare
  command and runs only from an approved plan that states what it does.
- Waits: run the condition once (`wait-for` exits 2 on a broken command), then wait on it
  with a tool made for waiting; never a poll loop, never an agent that ends its turn to wait.
  One monitor per subject, stopped when its work ends.
- Delegation: a builder brief carries `files:` and `check:`, a prod-probe brief `access:`
  (the route to every system it reads); the spawn hook refuses the spawn without them.
- `secretctl` (secrets) manages the vault (`list` / `run` / `each` / `import`; `run` and `each` pass
  `$SECRET_VALUE` by reference). `secret-vault-gate` refuses direct reads of the vault,
  Secret payloads and known values in tool inputs; `redact-secrets` masks known values and
  key-shaped tokens in Bash, Read, Grep, Glob and Agent results.
- A hook `ask` does not prompt in bypass-permissions mode.

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
