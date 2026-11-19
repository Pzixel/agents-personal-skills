# The destructive gate guards against agent mistakes, not deliberate bypass

## Decision
The deny-destructive hook stops an agent that errs (a wrong path, a forgotten WHERE, a kill of the wrong process, a write to the wrong host), not one that deliberately builds a command to get past it. A refusal ends with a line telling the agent not to work around it (other spellings, wrappers, eval, variables, scripts) and to use the named tool or a plan, except a refusal that names its own remedy (an unpaired quote, unreadable stdin, a same-command SQL file, the work budget): it carries only that remedy.

## Why
Each rule against a deliberate construction adds parser surface and false positives and clears none of an unbounded set. A false positive costs an owner interruption each time; a deliberate bypass needs an agent that ignores an explicit instruction, which instructions and review handle.

## Findings recorded and not fixed (deliberate constructions)
A COMMIT hidden ahead of a read-only marker; psql `-v` interpolation; a second `BEGIN READ WRITE`; `SET transaction_read_only`; `COPY ... TO`, `LOAD` or quoted function names behind a read-only marker; `INTO OUTFILE` under ClickHouse `readonly=1`; `clickhouse local -C <config>`; `eval` rebinding a name or `eval 'echo ... | sh'`; `docker container exec` / `docker run`; psql clusters such as `-1At`; client shell escapes other than psql `\!`, `\o |`, `\g |`.

## Exception: a bare production deletion never passes unclassified
A bare deletion is refused toward a plan; its `# irreversible:` line decides a SQL drop's `APPROVE` (`destructive-plan-asks-the-owner-only-...`). Where a command's shape, not a deliberate construction, hid a deletion, the destructive decision alone reads the command as the shell does:
- quotes and backslashes in a comment are comment text;
- a `$( )` inside double quotes is a fresh quote context;
- a heredoc operator with no terminator line hides nothing after it; a delimiter that is no identifier (`<<\SQL`, `<<'END-SQL'`) is read as the shell reads it;
- SQL on a client's stdin (also behind `kubectl exec`, `docker exec`, `ssh`, `gosu`, `su -c`, `sh -c`) is read from a literal heredoc or here-string, `< path`, a `$(cat path)` body or one `echo`/`printf`/`cat path` before the pipe; any other feed asks: pass the SQL as a literal heredoc attached to the client;
- a SQL file whose path the command also names elsewhere asks: write the file in one Bash call, run the client in the next;
- psql `\!`, `\o |`, `\g |` are classed by the command they run, also behind `-Atc`; psql `-q`/`-e`, mysql `-q` take no query;
- a quote the hook cannot pair asks: put the quoted text in a file or a quoted heredoc;
- one work budget, 300,000 characters plus 128 per text read; past it the hook denies with its remedy (a script file run in a separate call). 300,000 = `command_max_bytes` (64 KB) x 4, the deepest ordinary re-read (`ssh h bash <<EOF`), rounded up: shell-held scripts pass to 64 KB, 24 KB under `ssh` with `$( )` on every line; at 400,000 `echo "` + `$(` x 500 takes 600 ms. Open bodies or `$(` read again grow with a power of the length (hook timeout 5 s). A carrier (`ssh`, `docker exec`, `kubectl exec`) inside three others is denied as nesting too deep.

A shape counts as closed only where a row of `orch/tools/tests/fixtures/destructive_probes.json` pins it as refused; every review probe is pinned, and one pinned allow is a known gap, a read control or a command the shell does not run.

Accepted: an escaped brace (`echo {a,b\}' ' | psql ... -c '<DROP>'`) leaves a quote unpaired and asks (3814e3e: deny; refused in every mode); `wc -l f; psql < f` asks (same-command file); a read through an unterminated heredoc asks (h01v9).

Price against 3814e3e (`replay.py destructive-diff`, breakdown in `ORIGINS.md`), other gates unchanged, no command at the budget: of 21,684 stored commands 111 change, 1 refused -> allowed (row 21406, `rm -rf` of a `/tmp` path), 79 newly refused; of 47,699, 55 change, 0 and 43 (one a real production `UPDATE`).

Rejected on both sets, retry only on new evidence: a raw-text floor (69 refusals, all false or local), a whole-text fallback for non-local clients (177, 150 reads); neither caught a real write. Not taken: ClickHouse `--readonly=2` as a read (on 25.8 `INSERT INTO FUNCTION url/file` writes).

## Known gaps (still allowed; rows pinned in the fixture)
A script written by a heredoc and run in the same command (A2, A3, A5); SQL through `xargs` or `$(cat file)` as an argument (P17, T10, T14); a read file rewritten under another spelling (T17, T18, X11, X12); stdin redirected into a group, subshell, function or `exec` (F07-F09, F24); a client file path on the far side of a carrier (G1, G2, G4); a heredoc delimiter `<<1`, `<<"END SQL"` or `<<$X`, no operator to the hook, feeding an exec'd client (T4a-T4c); `review_probes` h01e (`os.system`), h03g (nested parentheses in a quoted `$( )`), h09a-h09c (a quoted or commented `<<WORD` swallowing lines), h11b (SQL path in a variable), h11l, h11m (mysql `source`, psql `\i`), h12a (backtick with a double quote in double quotes), h12f-h12i (`su-exec`, `runuser`, `ash -c`, `docker-compose exec`), h12l (heredoc into a multi-line group), h12m (backslash-newline in a word), p12 (`exec 3<`, `<&3`). p21 (pipe into `clickhouse-client -q 'SELECT 1'`) is no hole if that stdin is only INSERT data (claim). A heredoc into a subshell asks even on a read (B15); `-f -` with nothing fed gets the file remedy (B05).

## Open
- Unverified: what Claude Code does with a command when the hook passes its 5 s timeout.
- Outside this change: the headroom gate misses a `kubectl exec` after a comment quote (fixture row X1).

Kept: docker global options (`-H`, `--context`) are skipped before the subcommand: a destructive query on a remote daemon is a mistake-shaped case.

## Resume signal
Reopen a finding when a recorded real agent command (not a probe) slipped past through it.
