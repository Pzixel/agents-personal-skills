# orch — a measured build-and-review flow for Claude Code

A local Claude Code plugin. For every code change beyond one line, the `/orch:orchestrate`
skill has one Opus session (`orch:builder`, effort `medium`) write a short spec, build the change
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
2. Wait. The main session spawns agents and reads their replies; small reads (under the
   thresholds of `main_budget` in `tools/orchestrate-params.json`) it does itself.
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

On macOS 13 or later, with Python 3.9 or later at `/usr/bin/python3` (hooks and tools start through it; on macOS it comes with the Command Line Tools), run `./install.sh claude` at the repository root ([README.md](../README.md#install-for-one-user)).
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
- `skills/worker-rules/` — preloaded into every agent in place of the user-level instruction
  files: `assemble.py` prints them without their owner-only regions
  (`decisions/orch-agents-get-worker-rules-not-the-user-instruction-files.md`).
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
  - `background-watch-gate.py` — Stop: no turn end with an open, unwatched background task.
  - `open-question.py` — PostToolUse PushNotification|AskUserQuestion, UserPromptSubmit, Stop: pins a `QUESTION:` notification to `~/.claude/open-questions/<session_id>.md` and removes the file on AskUserQuestion, a typed owner prompt, or a stop with nothing running; never blocks. It shows only where the user's `statusLine` setting points at it: `"statusLine": {"type": "command", "command": "/usr/bin/python3 ~/.claude/tools/open-question-statusline.py"}` in `~/.claude/settings.json`.
  - `monitor-wait-note.py` — waiting protocol as context on every Monitor call.
  - `scout-bounds.py`, `bash-output-clamp.py` — output clamping.
  - `offline-gate.py` — offline lock on production access.
  - `secret-vault-gate.py`, `redact-secrets.py` — secret protection.
  - `deny-destructive.py`, `plan-approval.py` — destructive commands and their approval.
  - `merge-gate.py` — landing on a shared branch.
  - `reply-lint.py` — final-reply lint.
  - `fresh-reader.py` — Stop, off by default: a fresh opus reader holds a number whose labels were never read.
  - `task-type-checklist.py` — per-task checklist injection.
  - `worktree-dirty-gate.py`, `worktree-reaper.py` — worktree hygiene.
  - SessionStart also starts `tools/tmp-evict --scheduled` detached; it does nothing until `tmp-evict enable`.
  - `session-tools-link.py` — `~/.claude/{tools,hooks}` links in a fresh HOME (eval runs).
  - `dup-skills-hook.py` — one context line when a skill or agent name is visible from two
    sources (`tools/dup_policy.py`; CLI `tools/dup-skills`), silent otherwise.
  - `memory-routing.py` — memory routing reminder; not wired in `hooks.json`.
- `tools/` — `gate_policy.py` (pure gate decisions), `orchestrate-params.json` (thresholds,
  spawn rights, build settings), `check-run`, `wait-for`, `pace` (measured minutes from the first builder event to the merge of each task's own PR for one repository, median per size tier; `--tasks` lists the tasks; rules in `pace_policy.py`), `rm-own` (removes an agent's own
  scratch paths after checking the real path; the destructive gate lets it through), `kill-own`
  (SIGTERMs the agent's own leftover local processes after the ancestry checks; same gate exemption), `secretctl` with `vault_policy.py`
  and `vault_files.py`, `scout.py`, `scout_policy.py`, `scout-mcp-empty.json`, `clamp.py`,
  `worktree_policy.py`, `tmp-evict` with `tmp_evict.py` and `tmp_evict_policy.py` (opt-in daily two-phase cleanup of
  repository tmp directories by idleness; `tmp-evict --help`; Codex sessions and a separate per-account transcript
  store not passed with `--projects` are not read, and a reader they hold does not bring an entry back: the
  `.evicted.tsv` row holds the `mv` line for the 14-day grace; an absolute reference written through a symbolic
  link above a repository does not reach its entry), `tests/`.
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
  about 3.4 s per stop (measurement in `ORIGINS.md`), capped by the 25 s judge timeout. A later stop
  (`stop_hook_active`) judges only the deferral family (deferral, next-step, timed-step) while no
  `reply-lint: (later stop)` return exists in this turn's transcript, so it returns at most once per turn,
  and not when the verdict finds a hand-off, plus the judge-free `first-cause` rule at most twice per turn (a `Proven:`
  opening is returned first for its unread records unless a line holds only `Unread: none` / `нет` / `ничего`, then for its form;
  backtest `orch/tools/backtest/first_cause_backtest.py`); hand-off,
  completion claim, lever and duration are first-stop only (a later stop answering a `stop-reason` return runs the
  deferral family even under a hand-off verdict), so a turn holds at most four reply-lint returns. Rule
  `stop-reason`: a main stop of the terminal client (`CLAUDE_CODE_ENTRYPOINT=cli`; every other value and an
  absent one exempt) with nothing of the session live must close with a section under a line holding only
  `Why work stopped` (`Почему работа остановлена` in a Russian chat); a reply without it is returned once
  (details "Gates"). Rule `duration`: a reply naming days or weeks goes to the judge (question F); a work
  duration with no measured-run basis is returned with the re-derivation text, and the AskUserQuestion gate
  refuses a question carrying one with the same text (backtest `replay.py duration`). A judge error,
  timeout or unparsable verdict passes the reply (fail
  open) and is logged in `~/.claude/reply-lint-log.jsonl` as `judge_error`, next to
  `rules` (the rules that fired) and `verdict`; a standing failure (no login, rate limit,
  broken install) fails open on every stop, so a silent check is diagnosed from that field.
- Fresh reader (`fresh-reader.py`, Stop, off unless the user's own rules file, `~/.codex/AGENTS.md` or `$CLAUDE_CONFIG_DIR/CLAUDE.md`, holds the line `Fresh reader: on`): on a main-session stop whose reply states a number after a main-thread data query (SQL `SELECT ... FROM`, `/api/v1/query`, a client of `fresh_reader.query_clients` in `orchestrate-params.json`, or a script written earlier in the session that holds one) that no earlier judged stop of the owner turn covered, and no HOLD returned earlier in that owner turn, an opus judge with no tools reads the owner turn, a 90k window of the earlier session and excerpts of cut-away results, and per number asks whether the meaning of each label behind it was read; `VERDICT: HOLD` returns the stop once with the read to run, or "not known yet" with what blocks it. What turning it on costs: one opus call on your own login per judged stop (36.8 a day, 515 in 14 days of one owner's sessions by the shipped fr-2 rule, an upper figure because no return is simulated; the prototype number rule gave 22 a day), a stop held up to the 60 s judge timeout (measured 4-33 s on the 48 judged turns at the shipped revision; hooks of the group run in parallel with reply-lint), a return to the agent on a HOLD, at most one per owner turn (shipped fr-2 revision on the 48 judged turns of retro-evals CASE-84: 17 HOLD and 31 PASS in a first run, 19 and 29 in a second, 4 of 48 verdicts differ between the two runs; of 13 turns labelled right the first run holds 11, of 6 labelled wrong it passes 4; the prototype held 13 of 40 backtest turns, 9 right, 4 wrong); the accuracy was measured on one owner's sessions only. A return also makes the next stop a `stop_hook_active` stop for the other Stop hooks: reply-lint skips its judge and worktree-dirty-gate skips on it, at most one such stop per owner turn. Not judged: a `stop_hook_active` stop, and a turn whose only data query ran inside a subagent (71 owner turns in 14 days, 5.1 a day; no verdict was measured on that shape). A judge error, timeout or malformed verdict passes (fail open); judged stops are logged in `~/.claude/fresh-reader-log.jsonl`. Backtest: `orch/tools/backtest/fresh_reader_backtest.py`.
- Destructive commands are classified by `deny-destructive.py`
  (`gate_policy.destructive_decision`, table in `orchestrate-params.json`), never by the
  agent. `ours` (paths under `/tmp`, `tmp/`, `.worktrees/`; k8s objects labelled
  `orch.session`; live writes with a cheap mechanical undo on one named object such as `kubectl scale`
  or `kubectl rollout restart|undo`; with `--all`, a selector, several
  objects or `-f`/`-k` they are `approval`)
  runs without a prompt; Flux reconcile/suspend/resume is unclassed and runs. `approval` (a live write whose
  undo is not mechanical: SQL writes, `kubectl apply/delete/patch`) goes into
  one plan script with `# scope / # undo / # irreversible / # count / # contradiction /
  # prod_position / # way_back / # effect_read` headers, run from the main session as
  `bash <abs path>  # DESTRUCTIVE_PLAN sha256=<hash>` (that one line plus whole-line `#`
  comments; a command whose top level carries the marker in comment position either is
  that form or is refused). The plan runs at once and the agent reports scope, count and
  undo right after (the hook says so in `additionalContext`), unless the owner is needed.
  `# irreversible:` is exactly `none` unless an effect with no way back touches something
  in use (something a client sees, an object or money at a vendor, a Secret value, data
  that code or people read); then it names that effect. Deleting data nothing reads is
  `none` only when `# undo:` or `# effect_read:` states the read that showed it; the hook
  does not parse that line. A value that opens with `none` and goes on is refused by the
  form. The owner is asked (the permission prompt, or `APPROVE <hash>` where the session
  cannot prompt, one run per approval) for a value other than `none`, or what the hook
  sees in the plan text or a pinned file's text: an `impossible` reading other than a SQL
  DROP, TRUNCATE or full DELETE (a Secret write, a namespace/PVC/CRD delete, a force push,
  rm or `find -delete` outside `ours`, a git discard outside a worktree), a privileged
  reading, a quote it cannot pair, or a reading over its work budget. A pin line alone no
  longer asks. No setting turns the owner back on for every plan. Replayed on the nine plan
  files that reached the owner in the first 15 hours of the previous rule, the built rule
  asks for 3 of 9 with the in-use stand-in (`decisions/destructive-plan-asks-the-owner-only-for-an-effect-with-no-way-back.md`).
  A way back is a step the plan states that returns the prior state (a saved dump the plan
  takes and names is one).
  `impossible` (no undo of its own: DROP/TRUNCATE, Secret replace, namespace/PVC delete,
  force push, rm outside `ours`) is refused as a bare command and runs only from a plan; the
  hook then decides whether the owner approves it, and the agent asks only when it says so.
- Waits: run the condition once (`wait-for` exits 2 on a broken command), then wait on it
  with a tool made for waiting; never a poll loop, never an agent that ends its turn to wait.
  One monitor per subject, stopped when its work ends.
- Delegation: a builder brief carries `files:`, `check:` and one `parts:` line (the task's parts, their order, the whole-task estimate, the part this builder owns; over `parallel.split_above_minutes` a builder owns one part, decisions/parallel-builders-for-declared-parts-over-one-hour.md), a prod-probe brief `access:`
  (the route to every system it reads); the spawn hook refuses the spawn without them.
- `secretctl` (secrets) manages the vault (`list` / `run` / `each` / `import`; `run` and `each` pass
  `$SECRET_VALUE` by reference). `secret-vault-gate` refuses direct reads of the vault,
  Secret payloads and known values in tool inputs; `redact-secrets` masks known values and
  key-shaped tokens in Bash, Read, Grep, Glob and Agent results.
- A hook `ask` does not prompt in bypass-permissions mode.

## Tests and evals

```
/usr/bin/python3 -m unittest discover -s tools/tests -q
claude plugin validate .
evals/run.sh iterate feature-small      # one run, with the plugin
evals/run.sh tierA                      # tier A and negatives, with and without, 3 runs
```

Details, cost and how to read the results: `evals/README.md`.
`tools/tests/test_usage_audit.py` loads `~/.claude/usage-audit.py`, which is not part of this
repository.
