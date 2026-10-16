# evals/private — cases built from private material (ignored)

Everything here except this file is ignored by git: tier B checkpoints are cut from real
session transcripts and tier C replays clone a private repository, so neither may be
tracked. The eval runner discovers cases by filesystem, so the ignored cases still run
(`evals/run.sh iterate tierB-<n>`, `--tag tierB`, `--tag tierC`).

## Regenerating

- **Tier B (checkpoint, `tierB-<n>/`).** Pick a decision point in a main-session
  transcript (`~/.claude/projects/<project>/<session>.jsonl`): the message that presents
  the decision material (a task spec, a reviewer's findings, an owner question). Cut from
  the preceding `compact_boundary` line (or line 1) to that message:
  `python3 evals/tools/cut_history.py <transcript> <start> <end> tierB-<n>/history.jsonl`.
  `case.yaml` names `context.history_file: history.jsonl`; `prompt.md` carries the
  frontmatter (`max_turns: 8`, `timeout_seconds: 900`, an `append_system_prompt` that
  says the repositories are unreachable in the replay) and the owner's real next message
  (or `Continue.`). Graders: one `tool_used: Agent` step grader and one result grader,
  criteria written from what the real session did next, with no private names.
- **Tier C (PR replay, `tierC-<repo>-<pr>/`).** A scaffold clones the local checkout at the
  replay base with no remote and no other refs, and copies into the workspace the toolchain
  the sandbox cannot read from `$HOME` (APFS clone-copy, cached under `cache/`). The prompt is
  the replay spec verbatim plus the worktree/check line and "Write the check output to
  check-output.txt"; the repository's check prints `check: passed|failed` last.
- **Current state.** This directory holds only this file in a fresh checkout; `run.sh full`
  then runs tier A alone. Scaffold helpers for tier C are built per repository and stay here,
  untracked.
