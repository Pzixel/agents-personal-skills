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
- a heredoc operator with no terminator line hides nothing after it; a delimiter that is no identifier (`<<\SQL`, `<<'END-SQL'`) is read as the shell reads it;
- SQL fed on stdin to the client's own command, also through `kubectl exec`, `docker exec`, `ssh`, `gosu`, `su -c`, `sh -c`, is read from a literal heredoc or here-string, `< literal path` before or after the client, a body `$(cat literal path)`, or one `echo`/`printf`/`cat literal path` before the pipe; a here-string or heredoc that expands, or a generator before the pipe, asks with one remedy: pass the SQL as a literal heredoc attached to the client;
- a file read that way whose literal path the command also names elsewhere (`<`, `-f`, `--file=`) asks: write the file in one Bash call and run the client in the next;
- a psql `\!`, `\o |`, `\g |` is classed by the command it runs, also behind bundled flags (`-Atc`); psql `-q`/`-e` and mysql `-q` take no query text;
- what the hook still cannot pair (a quote left open in the command text) asks: put the quoted text in a file or a quoted heredoc.

A shape counts as closed only where a row of `orch/tools/tests/fixtures/destructive_probes.json` pins it as refused.

Price on the stored commands against 3814e3e (`replay.py destructive-diff`; breakdown: `ORIGINS.md`), every other gate unchanged on every row: 21,684 commands of 2026-11-13..17, 1 refused -> allowed on the stored commands (a `rm -rf "$w"` of a `/tmp` path), 79 newly refused (50 same-command SQL file, 19 unreadable stdin); 47,699 commands of 2026-11-05..12, 0 refused -> allowed, 43 newly refused (22 same-command SQL file, 9 unreadable stdin, one real production `UPDATE` caught).

Rejected on both sets (69,383 commands); retry only on new evidence: an unconditional raw-text floor refused 69, all false or local; a whole-text fallback for a client on a non-local target with no attributed SQL refused 177 (150 reads, 9 local writes, 2 mentions, 16 unread); neither caught a real write.

Candidate not taken: ClickHouse `--readonly=2` as a read; on 25.8 it let `INSERT INTO FUNCTION url/file` write (`ORIGINS.md`, "Destructive-gate false positives").

## Known gaps (still allowed; pinned in `orch/tools/tests/fixtures/destructive_probes.json`, group `gap`)
- A script written by a heredoc and run in the same command (A2, A3, A5).
- SQL passed through `xargs` (P17) or as `-c "$(cat file)"` / `ssh host "$(cat file)"` (T10, T14).
- A file the client reads, rewritten under another spelling: a glob loop, a generator script, `mv` of its directory, `sed -i` over a glob (T17, T18, X11, X12).
- Stdin redirected into a group, subshell, function or `exec` that runs the client (F07, F08, F09, F24).
- A client file path read on the far side of a carrier, where the local file the hook reads is not the remote one (G1, G2, G4).
- Group `review_probes`, allowed at 3814e3e and now: h01e (Python `os.system`), h03g (nested parentheses in a quoted `$( )`), h09a-h09c (a quoted or commented `<<WORD` swallowing lines), h11b (SQL file path in a variable), h11l, h11m (mysql `source`, psql `\i`), h12a (backtick with a double quote inside double quotes), h12f-h12i (`su-exec`, `runuser`, `ash -c`, `docker-compose exec`), h12l (heredoc into a multi-line group), h12m (a backslash-newline inside a word), p12 (`exec 3<` with `<&3`). p21 (a pipe into `clickhouse-client -q 'SELECT 1'`) is no hole if stdin there is only INSERT data (claim).
- Boundary rows: a heredoc into a subshell asks even on a read (B15); `-f -` with nothing fed gets the file remedy (B05).

Open item, outside this change: the headroom gate misses a `kubectl exec` after a comment quote (fixture row X1).

Kept: skipping docker global options (`-H`, `--context`) before the subcommand, because an agent that legitimately targets a remote daemon and runs a destructive query there is a mistake-shaped case.

## Resume signal
Reopen a finding when a recorded real agent command (not a constructed probe) slipped past the gate through it.
