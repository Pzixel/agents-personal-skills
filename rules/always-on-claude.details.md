# Always-on rules for Claude Code: procedures

Companion to [`always-on-claude.md`](always-on-claude.md). Each section is read when its rule applies or when the named hook refuses an action. The hooks belong to the `orch` plugin ([`orch/hooks/`](../orch/hooks)).

## Gates

- Real-path check before a change reaches a shared path: service start plus one unit through natively, against the shared local store container or a production read route, never a container per task; on a numeric path also the target before/after. The merge command carries `CHECK_RAN: <what ran, result>`, `PROD_POSITION: <blast-radius dimension, production's number, source>` and `WAY_BACK: <undo, minutes>`, plus `ALWAYS_ON: <before> -> <after> chars` (wc -c) with `removed: <text cut>` or `reason: <why it must grow>` when it grows an always-on rules file (`rules/always-on.md`, `rules/always-on-claude.md`), or `merge-gate` refuses it.
- Stop hook `reply-lint`: returns once a reply that leaves work waiting on the owner's choice, answer or approval in plain text (a small-model judge, called only when the reply addresses the owner; inside my authority: decide; otherwise AskUserQuestion with each option self-contained, never a pointer to an earlier message; an action only the owner can perform, such as a typed `APPROVE <hash>`, stays text with the exact action), a completion claim without its observation, and a first-person promise of later work with no issue, PR or handoff file naming it. The return asks for an addendum with only the missing part, not a repeat of the reply.
- A hook `ask` does not prompt in bypass-permissions mode. A Claude Code config directory other than `~/.claude` (set by `CLAUDE_CONFIG_DIR`) does not load hooks from `~/.claude/settings.json`.
- `loop-cost-gate` (PreToolUse, PostToolUse, PostToolUseFailure on Bash; main session and agents): journal `~/.claude/loop-cost/<sha1 of the repository root>.jsonl`, all worktrees pooled, rows older than the 6 h window ignored. Key: basenames of the programs run after shell comments, wrappers (`cd`, assignments, `env`, `time`, `nohup`, `timeout`, `nice`, `caffeinate`, `sudo`, `xargs`, `stdbuf`, `rtk`, `check-run --`, `wait-for ... --`, `secretctl ... --`, interpreters and their flags, `sh -c`), pipe filters and small file tools are removed. A `wait-for` is journaled (its minutes count toward the repository's total) but never refused. The refusal folds the window by key and lists prior acknowledgements; the `# LOOP_COST:` reason is journaled as an acknowledgement for that key and restarts its count and the repository's minutes; a subagent carries the reason into its reply's deviations. Defaults in `orchestrate-params.json` `loop_cost`.

## Destructive

Classified by the orch hook `deny-destructive.py` (policy `gate_policy.destructive_decision`, table in `orchestrate-params.json`), never by the agent; no text an agent writes raises a decision.

- `ours`: paths under `/tmp`, `tmp/`, `.worktrees/`; k8s objects with the `orch.session` label; live writes with a cheap mechanical undo on one named object (`kubectl scale`, `kubectl rollout restart|undo|pause|resume`; with `--all`, a selector, several objects or `-f`/`-k` they are `approval`). Runs without a prompt. Flux reconcile/suspend/resume is unclassed and runs.
- `approval`: a live write whose undo is not mechanical (SQL writes, `kubectl apply/delete/patch`). Runs only from one plan script with `# scope / # undo / # count / # contradiction / # prod_position / # way_back` headers, run from the main session as `bash <abs path>  # DESTRUCTIVE_PLAN sha256=<hash>`, after the owner's approval of the plan: the permission prompt, or `APPROVE <hash>` where the session cannot prompt.
- Reads pass unclassed when the server enforces them: `clickhouse-client --readonly=1`, or psql SQL that starts with `BEGIN READ ONLY;` and has no COMMIT, ROLLBACK, SET TRANSACTION, `transaction_read_only`, RESET ALL or backslash command (no `-1`). A job of the same command stops with `kill $!`, `kill %1` or a name bound to `$!`.
- `impossible`: no undo (DROP/TRUNCATE, Secret replace, namespace/PVC delete, force push, rm outside ours). Refused as a bare command; it runs only from an approved plan that states what it does.

## Waits

A wait on a condition: run the condition once (`wait-for` exits 2 on a broken command), then one Monitor in the main session (up to 60 minutes; re-arm on the same run if it times out while the job lives). A wait until a clock time, or longer than one Monitor (a deploy window hours away, a nightly pass): one `CronCreate` one-shot at that time, session-only, its prompt carrying the check and the steps or the handoff path; `/schedule` when the session may close before then. Never an LLM that ends its turn to wait; never a poll loop, a `sleep` until the time, or monitors re-armed every hour. The reply names the mechanism: tool, task id, fire time, `CronList` to see it, session exit as what kills it, and the handoff file for that case.

## Secrets

The vault is managed by `~/.claude/tools/secretctl` (`list` / `run` / `each` / `import`; `run` and `each` pass `$SECRET_VALUE` by reference). `secret-vault-gate` refuses direct reads of the vault, Secret payloads and known values in tool inputs; `redact-secrets` masks known values and key-shaped tokens in Bash/Read/Grep results.
