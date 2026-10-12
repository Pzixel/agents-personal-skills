# Отчёт: скилл `dragonfly-scripting`

> Расположение (перенесено 2026-09-16 без git-истории): скилл — `../dragonfly-scripting/`; `lab/`, `research/`, `SOURCES.md`, `dragonfly-scripting-workspace/` — в этой папке `dragonfly-scripting-workbench/`. Пути `dragonfly-scripting/...` ниже читать относительно корня репозитория `agents-personal-skills`.

## 1. Что это и где лежит
Скилл: `dragonfly-scripting-workbench/dragonfly-scripting/` — `SKILL.md` (161 строка), 7 файлов в `references/`, 4 скрипта в `scripts/`. Цель — Dragonfly **v1.34.0**.
Область (из `SKILL.md`): Dragonfly + любой серверный скрипт — Lua, `EVAL`/`EVALSHA`, `redis.call`, `SCRIPT LOAD`/`LATENCY`, счётчики `eval_*_coordination_total`, хештеги, `--lock_on_hashtags`. Типовые запросы: скрипт замедлился или просел p99; ревью и переписывание Lua; выбор между Lua, пайплайном, MULTI и нативной командой; трактовка счётчиков и вывода latency; размещение ключей ради shard-local; батчинг. Не для эксплуатации Dragonfly без скрипта (деплой, репликация, память) и не для чистого Redis. Главный тезис: цена скрипта определяется тем, **где лежат его ключи**, и один и тот же Lua стоит за `redis.call` примерно в 27 раз дороже в одном режиме, чем в другом.

## 2. Два режима исполнения, с числами
Одинаковые 256 `redis.call` (HGET, значения 2048B, `--proactor_threads=4`, 200 итераций после 20 прогревочных), `lab/RESULTS.md` Q5:

| режим | форма | µs/`redis.call` | p50 скрипта | счётчики на вызов |
|---|---|---|---|---|
| shard-local | 1 ключ, atomic | **1.0** | 259.5 µs | shardlocal 1.00 / io 0.00 |
| io-coordinated | 8 ключей / 1 хештег | **26.9** | 6899.0 µs | shardlocal 0.00 / io 1.00 |
| io-coordinated | 8 ключей / 8 хештегов | **27.0** | 6912.7 µs | shardlocal 0.00 / io 1.00 |

Отношение ~27x. Ключевой факт: общий `{hashtag}` **сам по себе не кладёт ключи на один шард** на дефолтных флагах — 8 ключей с одним тегом и 8 ключей с восемью тегами стоят одинаково (26.9 vs 27.0 µs/call). Тег становится единицей блокировки и размещения только под `--lock_on_hashtags`.
Различать режимы надо не гаданием: `INFO ALL` до и после одного вызова, разница `eval_shardlocal_coordination_total` против `eval_io_coordination_total` — ровно один растёт на 1 за вызов (Q5, Q10); `scripts/bench_script.py` печатает этот split сам. `CONFIG GET lock_on_hashtags` на v1.34.0 возвращает пустой ответ (наблюдение 2026-09-16, lab primary), так что флаги живого сервера читаются из аргументов процесса. Для одного хеша (shard-local, Q1) `redis.call` стоит 5.1 µs при N=32 и 0.7 µs при N=8192.

## 3. Четыре измеренные находки на рабочей нагрузке
Workload: production Lua scripts from a private service, scrubbed copies in `dragonfly-scripting-workspace/evals/inputs/` and `lab/variants/`.

**a) Батчинг в `claim_mailbox_batch`** (Q7): 1024 просроченных кандидата, батч 32, записи ~2048B, все 14 ключей под тегом `{inbound}`. Per-candidate `HGET` → чанкованный `HMGET` + отложенный payload: **p50 41182.7 → 7813.9 µs (5.3x)**, p99 52222.5 → 17149.5 µs, ответы идентичны поле в поле (после нормализации двух серверных clock-полей), `tx_shard_polls` за 200 вызовов 663192 → 49792. Q10 меряет ту же пару back-to-back: **7.6x** на дефолте (54947.5 → 7223.5 µs) и **2.6x** под `--lock_on_hashtags` (2243.0 → 862.2 µs). Абсолютные числа брать из Q7, отношения — из Q10, не смешивать.

**b) Per-grant циклы `HGET` вместо `HMGET`** в `apply_new_mutable_mailbox_page.lua:453` and in another script of the source repository — тот же дефект формы, что в (a): N отдельных `redis.call` там, где хватает одного multi-arg. Цена формы измерена в Q1 (один хеш, 1.2x при N=8192) и Q7 (много ключей, 5.3x); отдельного замера именно этих двух строк в `lab/RESULTS.md` нет.

**c) 32 MiB JSON replay-plan на success-пути** (Q4, 50 итераций после 3 прогревочных, `--maxmemory=2048Mi`): `cjson.encode`+`HSET` стоит p50 1904.6 µs при 1 MiB и 15069.9 µs при 8 MiB. На 32 MiB **первый** encode+HSET прошёл (33766783 байта), а повтор упал с `-ERR Out of memory`: перезапись 32-мегабайтного поля требует старого и нового блоба в памяти одновременно. Параллельный `GET` тут остался субмиллисекундным (p99 375.1 µs при 32 MiB против 155.1 µs в покое), но величину блокировки из этой таблицы читать нельзя — контролируемый замер head-of-line в Q8.

**d) Компромисс `--lock_on_hashtags`** (Q5, Q10, Q11d): флаг переводит одно-теговый скрипт в shard-local (счётчики 1.00/0.00) и даёт крупный абсолютный выигрыш — Q7 orig 54947.5 → 2243.0 µs, Q3 74627.4 → 1555.9 µs, Q2 3936.3 → 214.4 µs. Плата — возврат head-of-line blocking: под 4 загрузчиками `EVALSHA` тривиального 1-ключевого скрипта, не трогающего их ключи, стоит **p50 7712.4 µs** на узле с флагом против **81.2 µs** на дефолтном (95x), хотя в покое оба отдают его за 115–125 µs. `SCRIPT LOAD` свежего текста 8894B: 499 → 478 µs на дефолте под нагрузкой, 440 → 7787 µs под флагом. Плюс флаг срезает выигрыш от батчинга: 9.9x → 2.0x (Q2), 7.6x → 2.6x (Q7).

## 4. Доказательство, что агент реально пользуется скиллом
`usage.json` есть только у arm-ов `with_skill`; у baseline их нет by construction.

| прогон | прочитано из скилла | запущено | взаимодействий с сервером |
|---|---|---|---|
| it-1 eval-1 | SKILL.md, execution-model.md, lua-patterns.md | `bench_script.py`, `lab.sh` | лаба поднята; в отчёте `eval_io_coordination_total +1.00 / eval_shardlocal +0.00` на вызов |
| it-1 eval-2 | SKILL.md, execution-model.md, lua-patterns.md | — | 0; REVIEW.md ссылается на `[lab Q3,Q4,Q5,Q7,Q9,Q10][S1]` |
| it-1 eval-3 | SKILL.md, lua-patterns.md, measurements.md | `lab.sh`, seed-пример как шаблон | 33 вызова docker/redis-cli, свой контейнер поднят и удалён |
| it-2 eval-1 | SKILL.md, measurements.md, lua-patterns.md, `bench_script.py`, spec+seed из `assets/examples/` | `bench_script.py --spec ... --seed ... --reseed --compare` | **5400** |
| it-2 eval-2 | SKILL.md | `lua_call_audit.py` на целевом .lua | 0 |
| it-2 eval-3 | SKILL.md, lua-patterns.md, measurements.md | `lab.sh`, seed-пример как шаблон | 33, контейнер `eval-3-ws-df` поднят и удалён |

Отдельно ценно: в eval-3 агент нашёл расхождение с `references/measurements.md` (`SCRIPT FLUSH` не сбрасывает `SCRIPT LATENCY` на v1.34.0) — значит читал reference, а не пересказывал его.

## 5. Доказательство, что скилл помогает
**Итерация 1** (`iteration-1/benchmark.md`, `ANALYSIS.md`): со скиллом **95.3% (22/23)** против **73.9% (17/23)**. По эвалам: 6/7 vs 6/7, 8/8 vs 6/8, 8/8 vs 5/8.
**Итерация 2** (`iteration-2/benchmark.md`, `ANALYSIS.md`): **25/25 = 100%** против **17/25 = 68%**, дельта **+0.32**. По эвалам: 8/8 vs 6/8, 9/9 vs 6/9, 8/8 vs 5/8 (eval 3 — копия результата итерации 1).
Перезамер латентности грейдером (`iteration-2/ANALYSIS.md`; один свежий контейнер v1.34.0, `--reseed --compare`, 30 итераций + 5 прогревочных):

| arm | original p50 / p99 | оптимизированный p50 / p99 |
|---|---|---|
| with_skill | 99270.6 / 153354.5 µs | 7446.3 / 10883.2 µs |
| without_skill | 68480.6 / 71497.2 µs | 6749.9 / 11164.3 µs |
| контроль original-vs-original | отношение p50 = 1.000 | — |

Arm-ы мерились **в разных прогонах**, original дрейфует между 68 и 99 мс, поэтому осмысленно только отношение before/after **внутри каждого arm-а**, а не сравнение arm-ов между собой. Оба оптимизированных скрипта оказались reply-identical.
**Токены и время.** Итерация 2: 59773 ± 8694 токенов со скиллом против 48053 ± 12443 без; итерация 1: 66320 ± 14039 против 51004 ± 13067. Длительности прогонов есть только для итерации 2: eval 1 — 445 с против 435 с, eval 2 — 320 с против 289 с; они сняты из уведомлений о завершении агента, а не измерены изолированно по настенным часам.
**Оговорки (`iteration-2/ANALYSIS.md`).** В eval 1 два различающих критерия проверяют дисциплину отчётности, а не качество результата. В eval 2 baseline-ревью опиралось на ложную посылку — ко-локацию по хештегу на дефолтных флагах.

## 6. Оптимизация триггера (description)
From the trigger optimization run (log not kept): 20 триггерных запросов, 12 train / 8 test (holdout = 0.4).

| точка | accuracy (held-out) | recall | precision |
|---|---|---|---|
| стартовая description (строка 20) | 50% | 8% | 50% |
| промежуточная (строка 50) | 58% | 17% | 100% |
| промежуточная (строка 80) | 50% | 8% | 50% |
| лучшая итерация цикла (строка 110) | **62%** | **25%** | **100%** |

На train лучшая итерация дала 61% accuracy / 22% recall (строка 97). Цикл **упал на итерации 5/5 по rate limit** (`RuntimeError: claude -p exited 1` из `improve_description.py`), поэтому `best_description` этой фазой применён не был.
Итоговая description: переписана вручную после ревью по правилам skill-creator — только условия «когда читать» и исключения, без истории создания и пересказа содержимого (83 слова, folded YAML `>-`). Loop-вариант (итерация 4, held-out 62%/recall 25%) и две ручные версии (logs not kept) дали 10/20: все should-not-trigger прошли, все should-trigger — нет. Harness `claude -p` засчитывает срабатывание только если ПЕРВЫЙ вызов инструмента — Skill/Read временной команды `dragonfly-scripting-skill-<id>`, поэтому его числа занижены и между вариантами не различают; при явном указании пути к скиллу (как в evals) он читался в 100% прогонов.

## 6a. Ревью качества по skill-creator
Независимое ревью (record not kept): PASS WITH FIXES — 2 блокера (устаревший `--help` у `script_latency.py`, ссылки на `lab/` вне скилла) и дубли/нарратив о происхождении чисел; всё исправлено в фикс-раунде (скилл самодостаточен: варианты `.lua` скопированы в `assets/examples/`, `SKILL.md` 161 строка, references с условием «читать когда»).

## 7. Источники
`SOURCES.md` — 93 строки по тирам: **A** (официальная документация) 30, **B** (исходники, design-доки, релиз-ноты, ответы мейнтейнеров в репозиториях от 500 звёзд) 39, **C** (инженерный блог вендора) 23, **D** (рецензируемая статья) 1; 6 записей отклонено. Правило доказательности: правило попадает в скилл только при Dragonfly-подтверждении — измерении `lab/RESULTS.md Qn` либо ссылке на документацию или исходник v1.34.0; Redis-only источники могут объяснять семантику Lua, но никогда не обосновывают правило про производительность.

## 8. Как повторить
**Лаба.** `lab/run_all.sh` поднимает `docker compose up -d --wait` (при необходимости профиль `single` на :6381), прогоняет все `lab/bench/q*.py` через `lab/.venv/bin/python` и перегенерирует `RESULTS.md`. Требуется заранее: `python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt` в `lab/`. Скрипт **не** гасит лабу — teardown отдельно: `docker compose -f lab/compose.yaml down -v`.
**Эвалы.** Промпты — `dragonfly-scripting-workspace/evals/evals.json`, метаданные каждого эвала — `iteration-2/eval-*/eval_metadata.json`. Агрегация: `python3 -m scripts.aggregate_benchmark <workspace>/iteration-2 --skill-name dragonfly-scripting`, просмотр — `python3 eval-viewer/generate_review.py ...`; оба из `~/.claude/plugins/cache/claude-plugins-official/skill-creator/b5439c41ae98/skills/skill-creator/`.
**Любое утверждение before/after** — только через `dragonfly-scripting/scripts/bench_script.py --spec ... --seed ... --reseed --compare <a.lua> <b.lua>` на идентичном засеянном состоянии, с проверкой равенства ответов и с p50 **и** p99.

## 9. Что не сделано
- Цикл оптимизации description не завершён (rate limit на итерации 5/5), новая description не применена.
- Скилл не установлен симлинком в `~/.claude/skills` / `~/.codex/skills` — этот шаг за главной сессией.
- Eval 3 в итерации 2 не перезапускался; засчитан его результат из итерации 1.
- По одному прогону на arm на эвал: внутриэвальная дисперсия не измерена.
- Eval 1 проверяет только ответы скрипта, а не пост-состояние; контрактом является `--compare`.
- Пункт 11 чек-листа в `dragonfly-scripting/SKILL.md` ссылается только на `[lab Q6][S1]`, без строки `[df-doc]`.
