Why: the skill's description exempts one-line edits; the typo (injected by the scaffold)
must be fixed in place with no Agent call and committed. Known noise: the trace regex for
`git ... commit` also matches a commit attempt that failed (the sandboxed `/usr/bin/git`
shim fails once in most runs), so it proves the attempt, not the commit.
