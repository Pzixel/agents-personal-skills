# Always-on rules: Claude Code

Supplements [`always-on.md`](always-on.md) with Claude Code mechanisms; Codex does not load it. `install.sh claude` imports it into the user-level `CLAUDE.md`. Procedures: [`always-on-claude.details.md`](always-on-claude.details.md) in this directory, read when the rule applies or a hook refuses.

## Memory

- A `user` or `feedback` memory that holds outside this repository goes into the user-level rules file (`~/.codex/AGENTS.md` or `CLAUDE.md`) as one line (merge with an existing line, keep its scope); otherwise into project memory. One owner per fact. A one-off request is not a global rule.

## Hooks and gates

- Hooks enforce the gates; obey a refusal's message, never work around it. The merge command carries `CHECK_RAN: <what ran, result>`, `PROD_POSITION: <blast-radius dimension, production's number, source>` and `WAY_BACK: <undo, minutes>`, plus `ALWAYS_ON:` when it changes an always-on rules file, or `merge-gate` refuses it. The Stop hook `reply-lint` returns once a reply that leaves a choice or answer to the owner in plain text (decide it, or ask through AskUserQuestion with self-contained options) and a completion claim without its observation (details "Gates").
- A turn that hands control to the owner ends with a message that describes the blocker in full (what it is, what clears it), never by pointer to an earlier message. When the blocker is the owner's answer, ask it through AskUserQuestion with each option self-contained, recommendation first; an action only the owner can perform (a typed `APPROVE <hash>`, a `!` command) stays text with the exact action.
- Destructive commands are classified by the hook, never by the agent: a live write with an undo goes through one destructive plan with one owner approval per batch; a write without undo is refused and the agent lists the skipped step (details "Destructive").
- One monitor per subject, stopped when its work ends, never duplicated, no heartbeat wake-ups. Before ending a turn with background work, a monitor is armed on that exact run; a restarted job needs a new one. A wait is a tool contract (details "Waits"), never an LLM ending its turn to wait.
- A delegate brief carries `files:`, `access:`, `size:`; the spawn hook refuses builders and probes without them (details "Delegation").
- `secret-vault-gate` refuses direct reads of the vault, Secret payloads, and known values in tool inputs; `redact-secrets` masks known values in tool results (details "Secrets").
