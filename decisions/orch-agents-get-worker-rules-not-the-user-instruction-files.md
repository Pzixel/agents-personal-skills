# Orch agents get the worker rules, not the user-level instruction files

## Decision

The five orch agents (builder, reviewer, scout, prod-probe, watcher) set `omitClaudeMd: true` and preload the `worker-rules` skill (`orch/skills/worker-rules/`). Its four `!` commands run `assemble.py 1..4`, which prints the user-level `CLAUDE.md` with its imports (one level) and the repository root `CLAUDE.md` with imports inside the repository, minus every well-formed owner-only region (`<!-- BEGIN owner only -->` .. `<!-- END owner only -->`). Text outside a region, an unmarked file and a malformed marker set are delivered. The text is cut into parts under 28,000 UTF-8 bytes; part 1 starts with `orch worker rules: begin` and every part ends with `orch worker rules: part N of M end`. Each agent's first sentence makes it call no tool and reply only `WORKER RULES MISSING` when a closing line is absent; the orchestrate skill reports that reply and does not retry.

Owner-only means addressed to the session that talks to the owner: reply shape, approvals, questions, waits, memory. Past sessions stay with the agents: reviewers and probes read transcripts. The public owner-only regions are marked in `rules/always-on.md` and `rules/always-on-claude.md`; a private rules file is marked by its owner.

## Why

Every agent paid at spawn for the whole instruction text and the project memory index although most of it addresses the owner's session. Numbers: ORIGINS.md, `orch`, "Worker rules".

## Compared against

- Worker regions marked instead of owner-only ones: a new rule appended outside a region would silently not reach any agent. Owner-only polarity makes every unmarked or malformed case deliver, one code path.
- One `!` command: the client replaces an output over about 30,000 characters by a 2 kB preview with the rest saved to a file. Four commands under a byte cap, each with its own closing line, turn a previewed part into a guard reply instead of silently lost rules.
- A broad grant (`Bash(sh:*)`) on the skill: it widened the main session after a Skill call. The grant is anchored to the assembler's quoted path, measured to stay narrow after a main-session Skill call.
- `disable-model-invocation` to hide the skill from the model: it breaks preload. A refusal in `skill-load-gate`: the hook never sees subagents, and no duplicate load was observed; the cost of one is accepted and counted from transcripts.
- Following project imports anywhere: a third-party checkout could print any readable file into an agent's context without the client's approval prompt; project imports resolve inside the repository only.

## Limits

Delivery rests on undocumented client behaviour (`!` in a preloaded plugin skill, `${CLAUDE_PLUGIN_ROOT}` in `allowed-tools`); a client update that drops it shows as `WORKER RULES MISSING` on every spawn. Not delivered: a project file when the working directory is outside any git repository, the managed policy `CLAUDE.md`, ancestor or nested `CLAUDE.md` files, `.claude/CLAUDE.md`, `CLAUDE.local.md`, `.claude/rules/`, imports deeper than one level. A line starting with `@` is an import only when it is wholly a path naming an existing file; otherwise it is delivered as text. A root `CLAUDE.md` resolving outside the repository, an import outside it, and a file that cannot be opened each deliver one notice line instead of their text; undecodable bytes arrive as U+FFFD. Text beyond four parts (about 111 kB) is not delivered and fires the guard; the measured real text was 56 kB in three parts; marking the private file adds headroom. The guard is checked once, before the agent's first tool call: after compaction the closing lines may be gone, and an agent that already started keeps working. Sessions started before an update keep their old agent definitions.

Not taken: the no-retry rule stays in the orchestrate skill only, although builder and reviewer hold the Agent tool (their bodies send work only to `orch:scout`, which carries the same guard); the effect on Codex of the visible marker comment lines in its installed block is unmeasured.

## Rollout

Before the push, the main session switches the main checkout to the branch head, starts a new headless session, spawns one real `orch:*` agent under the user-level skills-directory link the installer creates, and reads its first request: the begin line, every closing line, worker bullets present and the owner-only probes of both public files absent. A failure there means no push. After landing, `install.sh codex` refreshes the installed block that carries `rules/always-on.md`; the main session reads the first-request transcript of the next real spawn against the marked sources for the same clause.

## Undo

Revert the commit: the agents load the instruction files again; the marker lines are inert comments.
