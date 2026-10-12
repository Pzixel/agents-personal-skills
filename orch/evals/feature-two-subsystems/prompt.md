---
tags: [tierA]
schema_version: "1.1"
runs: 3
max_turns: 60
timeout_seconds: 1800
allowed_tools: [Agent, Skill, Read, Bash, Write, Edit, Grep, Glob]
name: feature-two-subsystems
description: a change across store, dispatcher and a new export format goes through the builder, two Fable reviews and a cross-check
expected_outcome: store keeps versioned history with update/history ops, export/import in CSV and JSON Lines round-trips, tests cover each part, bash check.sh passes with output in check-output.txt, committed; built by orch:builder, reviewed by two orch:reviewer spawns on Fable and cross-checked
---

Нужно три связанных изменения в stats_svc, одним PR:

1. Store: версии записей. Новый метод `update(id, value=None, tags=None)` меняет запись и сохраняет предыдущую версию; `history(id)` возвращает все версии от старой к новой с номером версии и монотонным счётчиком изменений store; `remove` оставляет историю доступной, а повторный `add` того же id после remove начинает новую цепочку версий, не теряя старую.
2. Dispatcher: операции `update`, `history`, `export`, `import`. Коды ответа по текущему контракту README; `import` атомарен — при любой невалидной строке store не меняется и ответ 400 с номером строки.
3. Экспорт/импорт: форматы `csv` (RFC 4180, теги через `;`, экранирование `;` и кавычек внутри тегов) и `jsonl`. `export` можно фильтровать по тегу и по версии (`latest` или `all`). Импорт того, что выдал экспорт, должен давать идентичный store, включая историю.

Покрой тестами каждую часть, включая граничные случаи экранирования и атомарность импорта. Обнови README-контракт. В конце `bash check.sh`, вывод в `check-output.txt`, коммит.
