# Always-on rules: Claude Code

Supplements [`always-on.md`](always-on.md) with Claude Code mechanisms; Codex does not load it. `install.sh claude` imports it into the user-level `CLAUDE.md`. Procedures: [`always-on-claude.details.md`](always-on-claude.details.md) in this directory, read when the rule applies or a hook refuses.

## Memory

- A `user` or `feedback` memory that holds outside this repository goes into the user-level rules file (`~/.codex/AGENTS.md` or `CLAUDE.md`) as one line (merge with an existing line, keep its scope); otherwise into project memory. One owner per fact. A one-off request is not a global rule.

## Past sessions

- A past Claude Code session named by ID, ID prefix or name is located and read with `~/.claude/tools/chat` (`find`, `humans`, `assistant`; `--help`), never with an ad-hoc transcript parser.

## Hooks and gates

- Hooks enforce the gates; obey a refusal's message, never work around it. The merge command carries `CHECK_RAN: <what ran, result>`, `PROD_POSITION: <blast-radius dimension, production's number, source>` and `WAY_BACK: <undo, minutes>`, plus `ALWAYS_ON:` when it changes an always-on rules file, or `merge-gate` refuses it. The Stop hook `reply-lint` returns once a reply that leaves a choice or answer to the owner in plain text (decide it, or ask through AskUserQuestion with self-contained options), and a completion claim without its observation (details "Gates").
- A turn that hands control to the owner ends with a message that describes the blocker in full (what it is, what clears it), never by pointer to an earlier message. When the blocker is the owner's answer, ask it through AskUserQuestion with each option self-contained, recommendation first, the question and options in the language of chat replies, since the question is a reply and not an artifact; an action only the owner can perform (a typed `APPROVE <hash>`, a `!` command) stays text with the exact action.
- Destructive commands are classified by the hook, never by the agent; the hook's refusal names the class and the step (details "Destructive").
- One monitor per subject, stopped when its work ends, never duplicated, no heartbeat wake-ups. Before ending a turn with background work, a monitor is armed on that exact run; a restarted job needs a new one. A wait is a tool contract (details "Waits"), never an LLM ending its turn to wait: a condition is one Monitor; a clock time, or a wait longer than one Monitor, is one `CronCreate` one-shot (session-only), never a poll script, a sleep or a chain of monitors. A reply that promises a later action by the agent names that mechanism, its id and fire time, how the owner can see it (`CronList`) and what kills it (session exit).
- `secret-vault-gate` refuses direct reads of the vault, Secret payloads, and known values in tool inputs; `redact-secrets` masks known values in tool results (details "Secrets").
- `loop-cost-gate` counts, per repository and across the main session and every agent, the minutes waited on long Bash commands (120 s or more), keyed by the program or script a command runs; another launch of a key is refused once that key, since its last acknowledgement, ran long 3 times, or ran long twice while all long commands of the repository since the latest acknowledgement took 30 minutes. Answer with a line `# LOOP_COST: <reason>` in the command naming the incremental form (resume from a saved point, the affected target only, one full run as the gate before merge) or why none exists (details "Gates").
- The destructive hook reads, before every `kubectl exec`, the target container's memory limit and working set and refuses when the started process cannot fit (a JVM: its `-Xmx` plus overhead; a JVM with no heap cap in the command: always); run such a process outside the pod (a throwaway client pod with its own limit, a local client over a port-forward), or state the heap and add a line `# HEADROOM: limit <L> use <U> adds <A>` for a process sized to fit.
