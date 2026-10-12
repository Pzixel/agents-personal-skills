#!/bin/bash
# Hard-cap finishing move, half of it. Commits whatever is already on disk
# with a fixed message plus an optional single-word slug — no free-text
# argument, so there is nothing here for a stressed agent to misquote.
#
# The handoff CONTENT does not go through this script. It goes through the
# normal Write tool to any *.md path — the turn-budget hook allows that too
# at hard cap — because Write takes file_path/content as structured fields,
# not shell text, so there is no escaping to get wrong. Do both in either
# order; each is independently allowed once the hard cap is reached.
set -euo pipefail

slug="${1:-checkpoint}"
case "$slug" in
  *[!A-Za-z0-9_-]*) echo "refused: slug must be letters, digits, - or _ only" >&2; exit 1 ;;
esac

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    git add -A -- . ':(exclude)orch-notes'
    git commit -m "checkpoint: turn budget reached ($slug)" || true
  fi
fi
echo "commit step done. Write your handoff-*.md now with the Write tool if you have not already."
