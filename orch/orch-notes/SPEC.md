# Worktree reaper
Goal: at SessionStart, detached, remove worktrees and local/remote branches whose tip equals a merged PR head across all GitHub main checkouts in ~/Documents/Repos; keep dirty/busy worktrees, the main checkout and the default branch.
Acceptance: policy unit cases (advanced tip kept; dirty/busy kept with branch; default never deleted; no merged PR kept; finished clean removed+local+remote); hook --dry-run logs "would" lines and executes nothing; settings.json valid with SessionStart entry.
check: python3 -m unittest discover -s tools/tests -q
No risk lines: deletion rules are fixed by the task contract; worktree remove without --force refuses on unexpected state.
