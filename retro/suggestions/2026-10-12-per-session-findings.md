# Per-session findings files for large scopes

## Observed

Third pass over 25 sessions (2026-09-15..10-12). The first two passes re-read
the same transcripts and found "new" problems each time; the owner stopped it:
"Прочитали чат — сделали выводы, больше к нему не возвращаться". One agent per
session (opus, high effort) then wrote a findings file with every mistake
(`~/.claude/retro/findings/<id>.md`, 833 rows), and the ranking was done from
those files only. Pointer: session 4520e5ad (example-service), 2026-10-12.

## Affects

Sources; Analysis (ranking).

## Proposed wording

Sources: "For more than about ten sessions, delegate one agent per session (a
long session in line ranges) to write `findings/<id>.md`: every mistake,
unranked, with line, time, what happened, what was right, cost proxy, rule
status. Later passes read findings files only; a transcript is read once."

Analysis: "Normalize findings with a fixed class vocabulary (one class per
mistake, closed classes for causes already fixed) before ranking; sum cost per
class, then rank the open classes."

# humans.py misses queued messages

## Observed

Messages the user types while the agent works are stored as `queued_command`
records, not `user` records; `humans.py` skips them (session 41e82fa3 L174,
L559, L740 — all three were owner corrections).

## Affects

Sources (scripts/humans.py).

## Proposed wording

"`humans.py` also prints `queued_command` records as human messages."
