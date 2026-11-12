# Repository tmp cleanup: by idleness, two phases (move, then delete), opt-in

## Decision

`orch/tools/tmp-evict` cleans `<repo>/tmp` and `<repo>/.worktrees/*/tmp`. The unit is a first-level
entry (one task's directory or file). A unit is idle when nothing wrote to it, no Claude Code
tool call named it and no tool call ran with its cwd inside it. Units of 100 MB or more idle for
more than 14 days, and smaller units idle for more than 180 days, are moved into
`<tmp>/.evicted/` with a row in `<tmp>/.evicted.tsv` that carries the restore command; 14 days
later they are deleted unless used since, in which case they are moved back. Never touched: a
directory holding `KEEP`, a unit holding or named by git-tracked files, symlinks. The pass runs
at most once a day from the SessionStart hook and only after `tmp-evict enable` on that machine.

## Why

Measured on one workstation, 2026-11-12, over 34 days of transcripts (the retention there):

- Size is concentrated: 35 of 1631 units were 100 MB or more and held 93.2 of 94.6 GB; the
  other 1596 units held 1.5 GB. Deleting small units buys nothing, so they are kept 180 days.
- Age is the wrong signal: 79 % of the bytes sat in units touched within 7 days.
- Another session used 5.9 % of units (252 of 4263, an upper bound: short names collide).
  Idle time before such a use: up to 1 day 95 units, 1-7 days 74, 7-14 days 49, 14-30 days 34.
  Use after more than 30 idle days is unknown (retention).
- Of the 35 large units, 8 were used again after 8-11 idle days and 1 after more than 14 days;
  that one was reached through a shell variable (`$E/tmp/<name>/...`) from another repository's
  session, which a path-only scan does not see.
- 8 of the 35 large units (68.6 GB) were named as `tmp/<name>` by git-tracked files of their
  repository (decision records, a README, a script's default data root): durable data living in
  tmp. They are exempt and printed by `tmp-evict --list-keep`.

The reference scan has blind spots that cannot be closed by parsing: readers that are not Claude
Code sessions, Codex sessions, references through a symlinked ancestor, per-account transcript
stores. The grace phase covers them: a wrong eviction costs one `mv` for 14 days.

## Compared against

- Delete by age: rejected, 79 % of bytes are in recently used units and old small files cost
  nothing.
- Delete at the idle threshold (one phase): rejected after the design review found the
  shell-variable witness and the tracked-file witnesses; a wrong liveness fact had no undo.
- A size budget with least-recently-used eviction: rejected, three units in active use held
  68 GB, so any budget below that evicts live work.
- A compressed archive tier: rejected, the bytes are model and embedding files that do not
  compress, and the text units are small.
- A launchd job: rejected on a probe; a LaunchAgent running the system Python got
  `PermissionError: [Errno 1] Operation not permitted` on a repository under `~/Documents`
  (macOS privacy protection). SessionStart runs in the terminal's context.
- An incremental reference index: not built; a full scan of 11.7k transcripts (8.4 GB) takes
  14-23 s detached.
- Name fallback only when the cwd resolution misses: rejected for always counting a relative or
  unresolvable `tmp/<name>` for every unit of that name; over-keeping costs disk, a miss costs
  data.

## Redo

`orch/tools/tmp-evict --dry-run` prints the plan and writes nothing. The measurement scripts
were scratch; the numbers above come from tool-call inputs of the transcripts joined to a walk
of the tmp directories.
