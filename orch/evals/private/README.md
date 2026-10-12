# evals/private — cases built from private material (ignored)

Everything here except this file is ignored by git: tier B checkpoints are cut from real
session transcripts and tier C replays clone a private repository, so neither may be
tracked. The eval runner discovers cases by filesystem, so the ignored cases still run
(`evals/run.sh iterate tierB-1`, `--tag tierB`, `--tag tierC`).

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
- **Tier C (PR replay, `tierC-<repo>-<pr>/`).** The scaffold clones the local checkout at
  the replay base, drops every other ref, and installs the toolchain the sandbox cannot
  read from `$HOME` (`lib/pyenv.sh`: a relocatable CPython and the project's
  site-packages, cached once under `cache/` and APFS-cloned into `.evaltools/`). The
  prompt is the replay spec verbatim plus the worktree/check line and
  "Write the check output to check-output.txt".
- **Cache.** `cache/` is rebuilt by the first scaffold that finds it absent.
- **Shared scaffold pieces (`lib/`).** `replay.sh` (`replay_checkout <repo> <base> <branch>`:
  clone at the base, no remote, no other refs, no reflog), `pyenv.sh` (CPython + a project's
  site-packages), `rust.sh` (`rust_tools_copy <channel>`: the pinned toolchain and the
  `~/.cargo` registry/git caches clone-copied into `.evaltools/`, offline cargo, clang and SDK
  paths, a workspace `TMPDIR`; writes `.evaltools/env.sh`), `node.sh` (Node plus
  `node_modules` unpacked from a tar keyed by the lockfile hash). A repository-specific
  `lib/<repo>-replay.sh` composes them and installs `.evaltools/check.sh`, which prints
  `check: passed|failed` last. `probe-<repo>-rust/` is the cheap sandbox probe of that setup.
