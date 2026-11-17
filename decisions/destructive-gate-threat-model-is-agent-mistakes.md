# The destructive gate guards against agent mistakes, not deliberate bypass

## Decision
The deny-destructive hook exists to stop an agent that errs: a wrong path, a forgotten WHERE, a kill of the wrong process, a write to the wrong host. It does not try to stop an agent that deliberately builds a command to get past it. Every refusal ends with a line telling the agent not to work around it (other spellings, wrappers, eval, variables, scripts) and to use the named tool or a plan instead.

## Why
Each rule against a deliberate construction adds parser surface and false positives while clearing none, and the set of constructions is unbounded. A recorded false positive costs an owner interruption on every occurrence; a deliberate bypass needs an agent that ignores an explicit instruction, which is a different failure handled by instructions and review.

## Findings recorded and not fixed (deliberate constructions)
- A COMMIT hidden in a comment or a string ahead of a read-only marker; psql `-v` variable interpolation; a second `BEGIN READ WRITE`; `SET transaction_read_only`; `COPY ... TO`, `LOAD`, or quoted function names behind a read-only marker.
- `INTO OUTFILE` under ClickHouse `readonly=1`; `clickhouse local -C <config>`.
- `eval` rebinding a name; `eval 'echo ... | sh'`.
- `docker container exec` / `docker run` spellings of an exec.
- psql option clusters such as `-1At`.
- SQL client shell escapes other than psql `\!`, `\o |`, `\g |` (`\i`, mysql `system`, sqlite3 `.shell`).

## Exception: a production deletion never passes unclassified
The owner relies on this hook as the guarantee that a real production database deletion waits for his `APPROVE` line. Where the shape of a command, not a deliberate construction, hid a deletion, the gate now reads the command as the shell does (the destructive decision only; every other gate keeps its reading):
- quotes and backslashes in a comment are comment text; an even or odd number of them no longer hides the commands around them;
- a `$( )` inside double quotes is a fresh quote context;
- a heredoc operator with no terminator line hides nothing after it;
- SQL fed on stdin to a client, also through `kubectl exec`, `docker exec`, `ssh`, `gosu`, `su -c`, `sh -c`, is read from a literal heredoc, `< literal path`, a body `$(cat literal path)`, or one `echo`/`printf`/`cat literal path` before the pipe; any other source asks with one remedy: pass the SQL as a literal heredoc attached to the client;
- a file read that way whose name the command also writes or names elsewhere asks: write the file in one Bash call and run the client in the next;
- a psql `\!`, `\o |`, `\g |` is classed by the command it runs;
- what the hook still cannot pair (a quote left open in the command text) asks: put the quoted text in a file or a quoted heredoc.

Price, measured on the 21,684 stored Bash commands of 2026-11-13..17 against 3814e3e (`orch/tools/backtest/replay.py destructive-diff`): 103 decisions change; one goes from refused to allowed (a `rm -rf "$w"` with `w` bound to a `/tmp` path); 72 newly refused: 50 same-command SQL file, 12 unreadable stdin, 5 CREATE, 2 DROP, 1 unpaired quote (a quoted word that spans lines after a heredoc operator on its line, which bash pairs and the hook does not), 1 SQL file not visible, 1 `eval`; every other gate returns the 3814e3e result on every row. 0 of the 21,684 commands ran a client shell escape (2 mention `\!` in text), so classing it costs nothing.

Candidate not taken: treating ClickHouse `--readonly=2` as a read cleared 56 of 76 newly asked rows in the design-stage replay of the same commands (not re-measured on the final code); the read that decides it is whether the server refuses `SET readonly=0` and every write under `readonly=2`. The 2026-10-23 measurement on ClickHouse 25.8 (`ORIGINS.md`, "Destructive-gate false positives") found `INSERT INTO FUNCTION url/file` writes under `readonly=2`, so the candidate stays out unless such a read on the current server version shows otherwise.

## Known gaps (still allowed; pinned in `orch/tools/tests/fixtures/destructive_probes.json`, group `gap`)
- A script written by a heredoc and run in the same command (A2, A3, A5).
- SQL passed through `xargs` (P17) or as `-c "$(cat file)"` / `ssh host "$(cat file)"` (T10, T14).
- A file the client reads, rewritten under another spelling: a glob loop, a generator script, `mv` of its directory, `sed -i` over a glob (T17, T18, X11, X12).
- Stdin redirected into a group, subshell, function or `exec` that runs the client (F07, F08, F09, F24).
- A client file path read on the far side of a carrier, where the local file the hook reads is not the remote one (G1, G2, G4).

Kept: skipping docker global options (`-H`, `--context`) before the subcommand, because an agent that legitimately targets a remote daemon and runs a destructive query there is a mistake-shaped case.

## Resume signal
Reopen a finding when a recorded real agent command (not a constructed probe) slipped past the gate through it.
