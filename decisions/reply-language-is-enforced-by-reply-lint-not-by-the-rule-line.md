# The chat-reply language is enforced by the `reply-lint` Stop hook, from the user's own preset

Decided 2026-10-22; preset source changed the same day on the owner's correction.

## Decision

`gate_policy.reply_lint_decision` fires the rule `language` when the prose of the final reply
(fenced and inline code, URLs, path tokens and table lines removed) holds no letter of the
chat language's script and at least 40 letters of other scripts. The chat language is the
user's own preset: the hook reads the line `Chat replies in <Language>` from the user-level
rules files (`~/.codex/AGENTS.md`, `~/.claude/CLAUDE.md`) at each stop and maps the language
name to a script (`gate_policy.SCRIPTS`, `_LANGUAGE_SCRIPT`). No such line, or a language
written in Latin script: the rule is off. This repository is shared across locales and
carries no user-specific preference; a language constant in the hook was the first version
and was removed on the owner's correction ("respect the local preset, do not force one
implementation").

The refusal text names no language: it says "the chat-reply language the owner's rules set",
as the 2026-10-20 hand-off refusal does.

## Why

The chat-language line was loaded in every session and did not hold once an English template
stood in front of the model: on 2026-10-22 three sessions answered the owner in English for
86-100% of their replies (retro-evals CASE-44; sessions 038ebb0f, c8993795, c7fe63a3). The
first English reply in each copied a template the harness or a rule supplied in English
(`lever #1: ...; blocker: ...; doing: ...`, "Readings: ...", "respond to the user in the
meantime"), and nothing pulled the thread back. A rule that restates the language is more of
the same text that already failed; a deterministic check at the moment of replying is the
lever.

## Compared against

- Rewording the templates in the chat language: the templates are rule text and hook text,
  English artifacts by the English-only rule; the failure also starts from tool meta text the
  owner does not control.
- A judge call: unnecessary, the script test is exact; a regex costs nothing per stop.
- Letter threshold 80 instead of 40: loses 46 of 76 true fires in the backtest and keeps 11
  false ones (harness texts), so the harness texts are excluded by prefix instead.
- Reading the owner's language from the transcript instead of the preset: session c8993795
  had one owner message, an English alert paste, and still owed a reply in the chat language.
- A language constant in the hook: rejected, the repository is shared across locales.
- Telling Latin-script languages apart (stopwords, diacritics): not built; no user of this
  repository has asked for it, and short status replies would defeat a diacritic test.

## Numbers (backtest 2026-10-22, 10 days of sessions)

182 non-harness sessions, 7250 replies. Known-bad sessions: 83 of 99 replies fire. All other
sessions with the owner writing in the chat language: 77 of 7244 fire, 76 English prose to
the owner, 1 false (a path with spaces). Sessions with an English-only owner (headless eval
subjects): 13 of 394 fire, all English permission complaints, the behaviour those evals
measure.

## Undo

Make `reply_script()` in `orch/hooks/reply-lint.py` return None (one line); the rule stays in
`gate_policy` unused. Minutes.
