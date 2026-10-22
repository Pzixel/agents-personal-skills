# Eval suite for plugin `orch`

`claude plugin eval` starts a fresh, isolated `claude -p` session per run with only this
plugin loaded (no user settings, CLAUDE.md, memory or MCP), scores it with the case's
graders and, under `--ablation with-without`, repeats the run without the plugin.

## Running

```
evals/run.sh iterate <case> [--keep-temp]   # 1 run, with-plugin arm only
evals/run.sh tierA                          # tier A + negatives, with and without, 3 runs
evals/run.sh full                           # tags tierA tierB tierC, 3 runs, with and
                                            # without, --max-cost-usd 300 -j 2 --keep-temp;
                                            # prints the projected cost first
```

Every mode passes `--trust-plugin --no-publish --scaffold --allow-tools Bash Write Edit` and
writes `evals/results/<mode>-<timestamp>.json` and `.html` (`evals/results/` is ignored).
Extra arguments go to `claude plugin eval` (`--max-cost-usd 5`, `--case 'feature-*'`, ...).
`EVAL_MODEL=<model>` pins the main-session model (`--model`); without it runs follow the account default.
`--keep-temp` keeps the run directory; the full trace is only there (`<dir>/out/trace.jsonl`,
`chmod 700 <dir> <dir>/sealed` first). Subagent records appear in the same trace with a
non-null `parent_tool_use_id`, and `tool_used` graders count their tool calls too; to grade
only the main session, use a `regex` on `trace` that requires `"parent_tool_use_id":null` on
the same record (see `feature-small/NOTES.md`).

A run that needs longer than one 10-minute tool call is started
detached and waited on: `wait-for --every 30 --max 570 -- test -s <result.json>`.

## Cost (measured 2026-09-28, default model, with arm, one run)

Negatives $0.08–0.16; bug fix, investigation, small feature $0.4–0.7; the two-subsystem
feature several dollars. These figures predate the builder flow (two Fable reviews and a
cross-check per code case), so code cases now cost more; re-measure on the next run.
`tierA` costs about 3 runs × 2 arms of that.

### How to get the real cost of a run

`costUsd` (per run and in total) is the session's `total_cost_usd`. It **includes every
subagent**: checked 2026-09-28 on a kept tier C run, the tokens of the main transcript plus
the four `subagents/agent-*.jsonl` equal the result's `modelUsage` per model exactly. The
dollars differ only in the price of the Opus model: the harness charges its tokens less than
the list table in `~/.claude/usage-audit.py` ($2.16 against $3.31 on that run; Fable and
Sonnet agree to the cent). Real cost = Σ over the main and every subagent transcript under
`<keep-temp dir>/config/projects/`, usage deduplicated per message id, of
(input × in + output × out + cache-write-5m × w5m + cache-write-1h × w1h + cache-read × cr) / 10⁶
at the model family's rates. `python3 evals/tools/run_cost.py <dir> [...]` prints it per
transcript with the harness figure beside it; `evals/tools/project_cost.py <tag> ...` projects
a suite run from the recorded `costUsd` means.

## What each tier proves

- **Tier A — process.** Given an owner-style task, the main thread loads `orch:orchestrate`
  and runs the flow: for a feature or a bug fix, `orch:builder`, then two `orch:reviewer`
  spawns on Fable (`mode: review`), then a cross-check (`mode: crosscheck`), all spawned by
  the main session, and the declared `check:` passes; an investigation goes to one
  `orch:scout` or `orch:prod-probe` and reports without fixing; `general-purpose` is never
  spawned.
- **Tiers B and C — private (`evals/private/`, ignored).** Checkpoint replays cut from
  real session histories (B) and replays of merged changes on a private clone (C). They exist
  only in the checkout that built them; `run.sh full` selects them by tag, and in a checkout
  without them it runs tier A only.
- **Negatives.** A knowledge question loads no skill and spawns no agent; a one-line edit
  is done in place. Both must score 1.0 in both arms.

Private material (transcripts, private repositories) is never tracked; how to build a
private case is in `evals/private/README.md`, the transcript cutter is
`evals/tools/cut_history.py`.

## Writing a case

One grader on the result (a file the run writes, e.g. `regex` on `check-output.txt`, or an
`llm` grader on the last message) plus one on the steps (`tool_used` with `input_match`),
plus at most one `arm: both` guard. Graders are `graders/<name>.md`; frontmatter lives in
`prompt.md`, `case.yaml` only names `context.scaffold_script`. Prompts go to Claude verbatim:
write them the way the owner writes tasks and keep the rationale in `NOTES.md`.
Scaffolds source `lib/mini.sh`: they copy the synthetic `fixtures/mini/` into the workspace
and commit it with a local git identity. The fixture is stdlib-only; its `check.sh` prints
`check: passed|failed` last and falls back to `/usr/bin/python3` because a `python3` shim
under `$HOME` does not run in the sandbox.

## Reading `aggregate-result.json`

- `aggregates.overallScore` — mean case score; `casesPassed`/`casesTotal` against
  `--threshold` (default 1.0); `meanDelta` — with minus without, per case in `cases[].aggregates.delta`.
- Per run (`cases[].arms.with[]`, `.without[]`): `score` (weighted share of graders passed),
  `passed`, `turns`, `costUsd`, `error` (scaffold failure, harness failure), `aborted`,
  `skippedPaidGraders` (llm graders skipped after `--max-cost-usd` was breached).
- `partial`/`partialReason` — the suite stopped early (cost ceiling, auth).
- **Rate-limit trap:** a rate-limited run scores 0 without `partial`. A run with 0 or 1
  turns and a zero score is a harness event, not a verdict: re-run it.

## Sandbox facts

Bash runs in the OS sandbox: `$HOME` unreadable, writes only in the workspace. `/usr/bin/git`
(the Xcode shim) fails there because it cannot write its cache; runs find another git
binary after a turn or two. `$CLAUDE_PLUGIN_ROOT` is empty in the model's Bash;
`hooks/session-tools-link.py` links `~/.claude/tools` and `~/.claude/hooks` in the eval HOME.
`context.add_dirs` accepts only paths inside the case directory, so a toolchain under `$HOME`
cannot be granted in place; scaffolds APFS clone-copy it (`cp -c`) into the workspace instead.
The eval directory may hold at most 20000 entries (the case is refused otherwise): keep large
caches under `evals/private/cache/` as one tar or few directories, and never leave a build
tree or a symlink into `~/.cargo` there, because discovery walks every file and follows links.
`/usr/bin/cc` works in the sandbox, `/usr/bin/git` does not (its xcrun cache path is not
writable even with `TMPDIR` set).
