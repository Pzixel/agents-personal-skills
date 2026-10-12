---
tags: [tierA]
schema_version: "1.1"
runs: 3
max_turns: 60
timeout_seconds: 1800
allowed_tools: [Agent, Skill, Read, Bash, Write, Edit, Grep, Glob]
name: feature-small
description: small feature in one subsystem goes through the builder, two Fable reviews and a cross-check
expected_outcome: summary() returns the percentiles, stddev and iqr, summary_by_tag exists, tests cover the boundary values, bash check.sh passes and its output is in check-output.txt, the work is committed; the change was built by orch:builder, reviewed by two orch:reviewer spawns on Fable and cross-checked
---

Расширь `summary()` в stats_svc, один PR:

- перцентили `p50`, `p90`, `p95`, `p99` по методу nearest-rank;
- `stddev` — стандартное отклонение генеральной совокупности;
- `iqr` = p75 − p25 (тоже nearest-rank);
- новая чистая функция `summary_by_tag(records)` → `{tag: summary(...)}` по каждому тегу, записи без тегов под ключом `""`;
- для пустого списка все новые поля `None`, как сейчас.

Тесты на граничные значения (1 запись, 2 записи, дубликаты, пустой список), README-контракт обнови. В конце прогони `bash check.sh`, вывод сохрани в `check-output.txt` и закоммить.
