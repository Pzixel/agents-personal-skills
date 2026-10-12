---
tags: [tierA]
schema_version: "1.1"
runs: 3
max_turns: 60
timeout_seconds: 1800
allowed_tools: [Agent, Skill, Read, Bash, Write, Edit, Grep, Glob]
name: bugfix-with-check
description: a bug fix from a one-screen spec runs the declared check
expected_outcome: remove of an unknown id returns 404 again, a regression test covers it, the declared check bash check.sh ran after the last edit and its output is in check-output.txt, committed
---

Баг: `handle(store, {"op": "remove", "id": "<нет такого>"})` отвечает 200 `{"removed": true}`, хотя по контракту README неизвестный id — это 404.

Цель: remove неизвестного id снова даёт 404, remove существующего — 200 и запись исчезает.
Приёмка: регрессионный тест на оба случая; остальные тесты зелёные.
Вне скоупа: любые другие изменения поведения.
check: bash check.sh

Вывод проверки сохрани в `check-output.txt`, закоммить.
