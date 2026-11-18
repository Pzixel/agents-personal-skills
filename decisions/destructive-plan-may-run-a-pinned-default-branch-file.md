# A destructive plan may run another file only when it is pinned and merged on the default branch

## Decision

`gate_policy._plan_decision` keeps refusing a plan over 41 lines (`plan_max_lines`) / 4000 bytes and a plan line that runs another file, with one exemption: a line that is exactly `bash /<abs path> [plain literal words]` (the word class of the plan marker's path: no whitespace, quote, backtick, `$`, `\`, `;&|<>(){}[]*?!#~^`), when the plan pins that path once with `# pin: /<abs path> sha256=<hex>`. `~/` is not accepted: the plan's shell expands it from a `HOME` an earlier line can set. The hook observes (`orch/tools/pin_read.py`): the real path, the file's sha256, its git blob id (`hash-object --stdin`) and the blob at the local `refs/remotes/origin/HEAD` tip (`ls-tree`), the tip's date, branch and the origin URL, under one 2 s budget for all git calls with repository-selecting variables (`GIT_DIR`, ...) removed and `GIT_NO_LAZY_FETCH=1`. The policy compares and asks only when the blob ids match, the mode is a regular file and the sha256 equals the pin; the prompt and the `APPROVE <hash>` request show file, sha256, origin URL, default branch with its tip hash and commit date. Four refusals, each naming its step: unreadable (fix the path), not on the default branch (track and merge the file there, or fetch), git failed or over budget (fix the repository or retry), sha256 differs (rewrite the pin).

A plan that holds a pin line holds nothing else that the shell could read as logic: every line is blank, a comment (first non-blank `#`), one of `set -e`, `set -eu`, `set -euo pipefail`, or a run line of a pinned path, whose argument words also may not start with `=` (zsh expands `=word`). Any other line is refused by number with "logic belongs in the pinned file"; so are a pinned plan with no run line, a pin never run and a path pinned twice. No allowed line can open a quote, a continuation, a heredoc, a function or a compound construct, so every run line is a fresh top-level command without reading shell syntax. The origin URL is shown without userinfo (up to the last `@` of the authority) and refused when it holds a control character; a plan holding a pin line and CR, VT, FF, `\x1c`-`\x1e`, `\x85`, U+2028, U+2029, NUL or U+FFFD is refused. A plan without a pin line is decided as before this change.

## Why

Two real plans (508 and 327 lines of bash) could not run; splitting them cost the owner about 7 approvals per task. With the bulk reviewed and merged, the owner approves one short plan that shows exactly which reviewed version runs.

## Compared against

- Per-step plans: about 7 approvals per task, each over a fragment.
- A higher line limit: the owner would read 508 unreviewed lines in a prompt.
- A per-line scan of quotes, backslashes, comments and heredocs deciding whether a run line in free-form bash starts a fresh command: dropped at review, 37 forms where it disagreed with bash 3.2.57 (data or a construct read as a fresh command), one a missed refusal; free-form bash is not classified per line by a small scanner.
- A whole-plan walker that also closes `run() { bash "$P" "$@"; }`: about 280 lines of code and 250 of tests (design rev 3), rebuilding shell reading that `fix/destructive-unparsed-commands` rewrites; a later task on that branch.
- A fetch in the hook: network inside a 5 s hook (hooks.json); a hook that times out lets the call go ahead, hence the in-process 2 s deadline (18-26 ms per pin measured warm).
- Byte compare via `ls-files` + `cat-file`: one more call and a partial-clone state; blob ids decide the same.

Check at landing: the 19 real plan files in the owner's checkouts decide the same before and after (none carries a pin).

## Residuals

- The local `origin/HEAD` is not fetched: a stale ref refuses a newer merged version and accepts a superseded or reverted one; a forged remote ref is out of the threat model (orchestrate-params.json `_doc`).
- Time of check to time of use: the file may change between the hook and bash.
- The pinned file's own inputs (files it reads or runs).
- The environment the plan inherits from the session: `PATH` decides which `bash` runs, an exported `BASH_ENV` runs before the pinned file.
- A function wrapper that runs a file (`run() { bash "$P" "$@"; }`) in a plan without a pin line: open before this change and unchanged by it.
