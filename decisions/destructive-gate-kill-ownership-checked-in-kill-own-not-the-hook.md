# Process ownership for kills is checked in kill-own, not in the destructive hook

## Decision
The destructive gate keeps refusing `kill <pid>`, `pkill` and `killall` (except signals to a job of the same command). Its refusal points at `~/.claude/tools/kill-own -f '<pattern>'` and `~/.claude/tools/kill-own <pid>`. `kill-own -f` resolves the pattern with `pgrep -f`, drops its own ancestors, and signals only when every matched process passes `gate_policy.own_kill_refusals`, at kill time.

## Rejected: resolve targets and check ownership inside the hook
The hook would run pgrep and the ownership facts (ps, launchctl, lsof, ps -E) for every command with a kill, then allow raw pkill/kill when every pid is the agent's own.
- Cost: about 120 lines across the hook, the policy and a shared fact module.
- Latency: 0.26-0.49 s per kill command, measured on a developer machine with ~600 processes.
- Failure mode: the hook timeout (5 s) cancels the hook, and a cancelled hook fails open, so a slow `lsof` turns the check into an allow.
- Race: pids resolved at hook time, signals sent at run time; a process started in between and matching the pattern is hit.
- Unmeasurable: the replay of recorded denials has no process tables, so the false positives it would clear cannot be counted offline.
kill-own already checks at kill time with no race, and `kill-own $(pgrep -f <p>)` passes the classifier today; `-f` only makes it one step.

## Resume signal
Reopen when transcripts show agents still issue raw pkill/kill after a refusal that names `kill-own -f`.
