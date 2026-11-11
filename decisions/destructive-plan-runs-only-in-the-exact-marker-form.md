# A command carrying the plan marker in comment position runs only in the exact form

## Decision

`gate_policy.destructive_decision` refuses (`deny`, class `approval`) a Bash command whose marker `DESTRUCTIVE_PLAN` stands, at the top level, in a shell comment outside heredoc bodies while the command is not the accepted form: one line `(bash|sh|zsh) <absolute plan path>  # DESTRUCTIVE_PLAN sha256=<64 hex>`, whole-line `#` comments allowed around it (the `# OUTCOMES:` and `# LOOP_COST:` lines other gates demand). A comment line starts with `#` after spaces and tabs only (NBSP, VT, FF, CR, `\x1c` or U+2003 before `#` make it a command). The path is `/` or `~/` followed by characters other than whitespace, quotes, backtick and shell metacharacters, so no quote carries a later line. The plan takes no arguments and redirects its own output; a `cd` goes into the plan file. A subagent gets the subagent refusal. A mention (quoted, a heredoc body, a grep) keeps its class. The hash and approval compare is unchanged.

## Why

Before, a launch off the form (redirect, `cd … &&` prefix, arguments, pipe, second command) was not seen as a plan run: no approval lookup, usually `allow`/`none`.

Measured 2026-11-11 by `python3 orch/tools/backtest/replay.py plan-form 28 --policy <gate_policy.py of a d6d4525 checkout> --labels <retro-evals tmp/plan-confirm-audit/risk-labels.tsv>`, distinct Bash calls (by tool_use id) of 28 days; every number is a line of its output. 256 token-bearing calls: 96 in the form, 92 off-form launches (23 sessions), 68 mentions (grows with sessions writing about the marker). At d6d4525 93 ran with no approval lookup (90 `allow`/`none`, 3 `allow`/`ours`): the 92 off-form launches, all refused now, and one plan line under a `# LOOP_COST:` line (session 8c437b03), now the accepted form and subject to the hash compare. 0 mentions change. 102 plan lines in the base regex shape, 0 stop matching the narrowed regex. Three of the seven IRREVERSIBLE plans in the labels ran only off-form: `r2-reencrypt-r3.sh`, `plan-drop-dead-payment-tables.sh`, `2026-11-11-inbound-kind-backfill.sh`.

Cost to the owner: of the 92 off-form launches, 51 had an earlier typed `APPROVE` of the same hash, 33 none, 8 a non-literal hash; up to 41 per 28 days newly need an approval (upper bound: a prompt approval leaves no transcript line). 31 pass arguments: 27 by the rule (a word after `<shell> <path>` on the marker line that is not a redirect, operator or comment) and 4 of the 5 wrapper launches with no shell-file match on the marker line (the fifth, c56ffb38 2026-11-04T00:47:49, passes only a redirect); each variant becomes its own plan file and hash. A reviewer's cross-check got 50 / 33 / 8; both counts reproduce, each at its read time (the difference is one launch made after its dump, session 0c69d357 18:26:58Z).

## Not covered (stated residuals)

- Marker absent: `bash <script>` is classed on its command line, the script is not read (3 token-less runs of a file the session launched with the marker).
- Inner-shell launches (top level only): `bash -c '…'`, `nohup sh -c '…' &`, `eval`, a heredoc into a shell, `echo … | bash`, the marker as a quoted argument are classed by the other rules.
- A wrapper script that runs the plan; commands started through the Monitor or CronCreate tools (deny-destructive matches Bash, Write, Edit, NotebookEdit); Codex sessions (no hooks installed); files the plan reads at run time (the hash covers the plan file only; a plan line that runs another file is refused).
- Known false refusal: the marker in comment position on a non-launch (`grep -rn foo orch  # where is DESTRUCTIVE_PLAN read`) or a form line in a heredoc body the gate does not treat as data; 0 rows in 28 days.
- The approval is spent when deny-destructive allows, before other gates answer (as at d6d4525): a background launch without `# OUTCOMES:` is refused by no-poll-gate after the approval is removed; the approval-request text says so. Reviewer count 0 of 55 plan allows followed by a deny; not cross-checked.
- Approvals typed before an off-form launch stay unspent: an approval binds the plan text's hash, so the worst case is one more run of an approved text.
- The replay's `--policy` needs `gate_policy.py` inside a base checkout (a lone copy fails to load).

## Rejected or deferred, with the deciding numbers

- Retry (approval valid again after a failed run): 0 of 3 measured retries reused a hash; every retry ran an edited plan.
- Stages (one approval for declared stages): 0 of 6 saved; all 12 launches of the one staged plan ran in the background, and a background run has no success signal to advance a stage on.
- Local scratch databases as `ours`: the closed grammar (`docker exec <allowlisted> psql … -c 'DROP DATABASE <name>'`) covers 0 of the 3 measured rows (loops over variables, pipes).
- Trusting the plan's `# undo:` line: the agent writes it.
- Relaxing `kubectl apply` by class: 2 irreversible and 2 read-only plans share it.
- Click approval through AskUserQuestion: deferred; who can answer the dialog (a hook, a prefilled `answers` value) and the hook-side `tool_response` shape are unread; a live probe decides both.
- Refusing any command that contains the marker as a substring: it would also refuse the mentions (68 rows, some refused by other rules); the comment-position rule refuses the same 92 launches and 0 mentions.
- Widening the marker to accept redirects and arguments: the approval covers one file's hash, not arguments or output routing.
