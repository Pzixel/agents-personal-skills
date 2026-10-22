# Subagent foreground waits are capped at 270 s, under the 5-minute prompt-cache TTL

Decision (2026-10-22): `wait_for_max_seconds` in `orch/tools/orchestrate-params.json` is 270 (was 570); the orchestrate skill's Waiting section says `--max 270`.

Why: every orch:builder and orch:reviewer request in the last 14 days (46 000 requests) carried the 5-minute cache TTL (`cache_creation.ephemeral_5m_input_tokens`), while the main session uses the 1-hour TTL. A foreground wait longer than 5 minutes lets the cache expire; the next request rewrites the whole context as cache creation. Measured over 2026-10-08..22 from the transcripts: 791 such rewrites, 81.7M tokens (builders 643, reviewers 150; Fable reviewers 12.1M tokens of their 72.1M cache writes), gap before the rewrite p10/p50/p90 = 6.2/9.1/18.5 minutes, only 2 rewrites after a gap under 5 minutes. A keep-alive turn (one wait-for call that returns) costs a cache read of the context, 5-50x cheaper than the rewrite.

Compared against:
- 1-hour TTL for subagents: not a harness setting we control, and it would not pay: 1h writes cost 2x base versus 1.25x for 5m; for Fable reviewers 60M x 2 > 72M x 1.25 even with the rewrites kept.
- Leaving 570 s and moving long checks to the watcher: the watcher is uncapped and cheap (Sonnet), but builders wait on their own cargo runs; the cap applies to them too.

Expected effect: the rewrite tokens drop to the share of gaps that come from single tool calls over 5 minutes (not measured separately). Re-measure with the same transcript scan after a week; if rewrites with gaps of 5-10 minutes persist, the cause is tool calls, not waits.
