# Always-on rules for Claude Code: procedures

Companion to [`always-on-claude.md`](always-on-claude.md). Each section is read when its rule applies or when the named hook refuses an action. The hooks belong to the `orch` plugin ([`orch/hooks/`](../orch/hooks)).

## Gates

- Real-path check before a change reaches a shared path: service start plus one unit through; on a numeric path also the target before/after. The merge command carries `CHECK_RAN: <what ran, result>`, `PROD_POSITION: <blast-radius dimension, production's number, source>` and `WAY_BACK: <undo, minutes>` or `merge-gate` refuses it.
- Stop hook `reply-lint`: returns once a reply that leaves work waiting on the owner's choice, answer or approval in plain text (a small-model judge, called only when the reply addresses the owner; inside my authority: decide; otherwise AskUserQuestion with each option self-contained, never a pointer to an earlier message; an action only the owner can perform, such as a typed `APPROVE <hash>`, stays text with the exact action), a completion claim without its observation, and a first-person promise of later work with no issue, PR or handoff file naming it. The return asks for an addendum with only the missing part, not a repeat of the reply.
- A hook `ask` does not prompt in bypass-permissions mode. A Claude Code config directory other than `~/.claude` (set by `CLAUDE_CONFIG_DIR`) does not load hooks from `~/.claude/settings.json`.

## Destructive

Classified by the orch hook `deny-destructive.py` (policy `gate_policy.destructive_decision`, table in `orchestrate-params.json`), never by the agent; no text an agent writes raises a decision.

- `ours`: paths under `/tmp`, `tmp/`, `.worktrees/`; k8s objects with the `orch.session` label. Runs without a prompt.
- `approval`: a live write with an undo. Goes into one plan script with `# scope / # undo / # count / # contradiction / # prod_position / # way_back` headers, run from the main session as `bash <abs path>  # DESTRUCTIVE_PLAN sha256=<hash>`. One owner approval per batch: native prompt where the session can prompt, otherwise the owner replies `APPROVE <hash>`.
- `impossible`: no undo (DROP/TRUNCATE, Secret replace, namespace/PVC delete, force push, rm outside ours). Refused; the agent lists the skipped step.

## Waits

Run the condition once (`wait-for` exits 2 on a broken command), then Monitor in the main session. Never an LLM that ends its turn to wait; never a poll loop.

## Delegation

A delegate brief carries `files:` (pointers from one Explore run), `access:` (route to every system read), `size:` (expected diff). Over ~400 LOC or more than one subsystem means slices, each with its own check. The spawn hook refuses builders and probes without these fields.

## Secrets

The vault is managed by `~/.claude/tools/secretctl` (`list` / `run` / `each` / `import`; `run` and `each` pass `$SECRET_VALUE` by reference). `secret-vault-gate` refuses direct reads of the vault, Secret payloads and known values in tool inputs; `redact-secrets` masks known values and key-shaped tokens in Bash/Read/Grep results.
