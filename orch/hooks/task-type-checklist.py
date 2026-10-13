#!/usr/bin/env python3
"""UserPromptSubmit hook: inject the first-artifact checklist for the task type.

Rules loaded at session start did not reach the decision point hours later
(retro 2026-10-12: per-unit counting, cheapest-check, thin-prototype and
wait-only-on-real-dependencies were loaded and not applied in 6 sessions,
while the same instruction placed in the prompt was followed every time).
This hook puts the 3-5 line checklist next to the prompt that starts the task.

Stdout is added to the model's context. Matching is by prompt words; several
types may fire. Short prompts without a task word get nothing. The incident checklist exists
because live failures were diagnosed before the last change was reverted.
"""
import json
import re
import sys

CHECKLISTS = [
    (
        r"((?:упал\w*|лёг|\bлег\b|лежит|лежат|не отвечает)(?:\s+\S+){0,3}?\s*(?:прод|production|сервис|service|бэкенд|backend|\bпод\b|pod|api|сайт|страниц)"
        r"|(?:прод|production|сервис|service|бэкенд|backend|сайт|api)\S*(?:\s+\S+){0,2}?\s+(?:упал|лёг|\bлег\b|лежит|лежат|не отвечает)"
        r"|(?<![\w#])(?:503|502)\b|\b(?:outage|incident|инцидент)\b|\bdown\b|(?-i:\bOOM\w*|CrashLoop\w*))",
        "Incident checklist (live failure; before any diagnosis):\n"
        "- First action restores the last known-good state: revert the last change on the failing path (code by PR revert, config through git, half-applied state cleaned).\n"
        "- Owner status within 5 minutes: what is broken for users, since when, what was reverted.\n"
        "- Only then the cause, as a hypothesis x cheapest-check table.\n"
        "- A mitigation that is reversible, bounded and ours is applied now, never listed as optional.",
    ),
    (
        r"(расслед|разбер|почему|не работа|не приход|не уход|сломал|упал|инцидент|incident|alert|алерт|отстава|lag\b|debug|why\b|investigate)",
        "Investigation checklist (first reply artifact):\n"
        "- Table: hypothesis x cheapest decisive check x result; at most 4 hypotheses; run the checks before any conclusion.\n"
        "- For a pipeline: one row per stage with produced / consumed / lag over the same window, before looking inside any stage.\n"
        "- Reproduce the claimed number from the primary source (5 raw records before any scan); note metric delay and the comparison base.\n"
        "- No fix, config change or measurement longer than a minute before the table names the surviving hypothesis.",
    ),
    (
        r"(оптимиз|ускор|производительн|пропускн|throughput|latency|\bперф|perf\b|бенч|bench|\d+\s*/\s*(?:с|s|сек|sec)\b|\brps\b|\bqps\b|(?:максим|целев|target)\w*\s+скорост|(?:cpu|память|memory|диск|disk|cost|стоимост)\W+(?:\S+\s+){0,3}?(?:сниз|уменьш|мень|cut|reduce|lower|limit|лимит|бюджет|budget|цел|target)|(?:сниз|уменьш|cut|reduce|lower)\W+(?:\S+\s+){0,3}?(?:cpu|память|memory|диск|disk|cost|стоимост|latency|задержк))",
        "Optimization checklist (first reply artifact; user-rules 'Optimization method'):\n"
        "- Name the unit of work at the customer boundary and the one metric: units/s or resource per unit there. Part metrics are diagnostics.\n"
        "- Unit-cost table: operation x minimum per unit by contract x measured actual x gap at target load; fix the top row; a code count is a hypothesis.\n"
        "- Candidates include the minimum-facts design built from zero; accept changes of cardinality before constants; hardware only after a saturation run.\n"
        "- First deliverable is the number from the thinnest prototype on the real path; guarantees after; re-measure the target after every change on the path.",
    ),
    (
        r"(дизайн|спроектир|план\b|плана\b|архитектур|схем[ау]|design|plan\b|предлож|вариант)",
        "Design checklist (first reply artifact):\n"
        "- Premise list: each premise with its source — contract / owner / measured / inherited from code / claimed by a client. Inherited and claimed premises are variables until verified.\n"
        "- Minimum derived from the contract, not from the current schema or code; the current behaviour and the cheap extension are always two candidates.\n"
        "- Proof work (prototype, harness, probe) is declared `proof` and runs without review ceremony; ship work is declared `ship`.",
    ),
    (
        r"(выкат|депло|deploy|rollout|раскат|включи|лимит|limit|канаре|canary|мигрир|migration|миграци)",
        "Rollout checklist (before the change reaches a shared path):\n"
        "- Blast radius in units: share of traffic, records touched, memory, tenants; the busiest instance decides the limit, not the quiet one.\n"
        "- Canary carries enough of the risk to show the defect; state what signal would show it and within how long.\n"
        "- Name the other session or owner working on the same system; one writer per shared system.\n"
        "- Rollback line ready before the change; proof of the original scenario on production before 'done'.",
    ),
    (
        r"(статус|что сделано|как успехи|как дела|чего жд|что происходит|status\b)",
        "Status format: 1) goal metric now vs target, measured when and how; 2) what changed since the last status, with evidence; "
        "3) the one blocker, whether it is a real dependency, and the resume signal; 4) next step. No narrative, no owner decisions.",
    ),
    (
        r"(^/loop|^/goal|продолжай|автоном|не останов|пока я не|loop\b|goal\b)",
        "Wake-up format (unattended work): first line 'lever #1: <the largest gap to the goal>; blocker: <none | real dependency + resume signal>; doing: <step on lever #1>'. "
        "Side work only after lever #1 is blocked by a real dependency named in that line. Never wait on a question to an absent owner; never idle on a permission prompt.",
    ),
]

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

prompt = payload.get("prompt") or ""
if len(prompt) < 12:
    sys.exit(0)

out = []
for pattern, text in CHECKLISTS:
    if re.search(pattern, prompt, re.IGNORECASE):
        out.append(text)
if out:
    print("\n\n".join(out))
sys.exit(0)
