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
- SQL client shell escapes (psql `\!`, `\g |cmd`, `\i`, mysql `system`, sqlite3 `.shell`): a tightening was built and reverted, since it cleared no false positive.

Kept: skipping docker global options (`-H`, `--context`) before the subcommand, because an agent that legitimately targets a remote daemon and runs a destructive query there is a mistake-shaped case.

## Resume signal
Reopen a finding when a recorded real agent command (not a constructed probe) slipped past the gate through it.
