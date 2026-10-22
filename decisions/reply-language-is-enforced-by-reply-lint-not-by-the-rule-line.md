# The chat-reply language is enforced by the `reply-lint` Stop hook, not by the rule line alone

Decided 2026-10-22.

## Decision

`gate_policy.reply_lint_decision` fires the rule `language` when the prose of the final reply
(fenced and inline code, URLs, path tokens and table lines removed) holds no letter of the
chat-reply script and at least 40 Latin letters. The script is a constant of the hook
(`REPLY_SCRIPT` in `orch/hooks/reply-lint.py`) placed next to the rule it enforces
(`~/.codex/AGENTS.md`, "Chat replies in Russian"). The refusal text names no language: it says
"the chat-reply language the owner's rules set", as the 2026-10-20 hand-off refusal does.

## Why

The rule line was loaded in every session and did not hold once an English template stood in
front of the model: on 2026-10-22 three sessions answered the owner in English for 86-100% of
their replies (retro-evals CASE-43; sessions 038ebb0f, c8993795, c7fe63a3). The first English
reply in each copied a template the harness or a rule supplied in English (`lever #1: ...;
blocker: ...; doing: ...`, "Readings: ...", "respond to the user in the meantime"), and nothing
pulled the thread back. A rule that restates "reply in Russian" is more of the same text that
already failed; a deterministic check at the moment of replying is the lever.

## Compared against

- Rewording the templates in Russian: the templates are rule text and hook text, which are
  English artifacts by the English-only rule; and the failure also starts from tool meta text
  the owner does not control.
- A judge call: unnecessary, the script test is exact; a regex costs nothing per stop.
- Latin threshold 80 instead of 40: loses 46 of 76 true fires in the backtest and keeps 11
  false ones (harness texts), so the harness texts are excluded by prefix instead.
- Reading the owner's language from the transcript instead of a constant: session c8993795
  had one owner message, an English alert paste, and still owed a Russian reply by the rule.

## Numbers (backtest 2026-10-22, 10 days of sessions)

182 non-harness sessions, 7250 replies. Known-bad sessions: 83 of 99 replies fire. All other
owner-Russian sessions: 77 of 7244 fire, 76 English prose to the owner, 1 false (a path with
spaces). Owner-English sessions (headless eval subjects): 13 of 394 fire, all English
permission complaints, which is the behaviour those evals measure.

## Undo

Remove the `reply_script` argument in `orch/hooks/reply-lint.py` (one line); the rule stays
in `gate_policy` unused. Minutes.
