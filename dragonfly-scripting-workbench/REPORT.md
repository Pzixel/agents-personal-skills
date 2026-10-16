# Report: the `dragonfly-scripting` skill

> Location (moved 2026-09-16 without git history): the skill is `../dragonfly-scripting/`; `lab/`, `research/`, `SOURCES.md`, `dragonfly-scripting-workspace/` are in this folder, `dragonfly-scripting-workbench/`. Read `dragonfly-scripting/...` paths below relative to the root of the `agents-personal-skills` repository.

## 1. What it is and where it lives
Skill: `dragonfly-scripting/` — at report time `SKILL.md` (161 lines), 7 files in `references/`, 4 scripts in `scripts/`. Target: Dragonfly **v1.34.0**.
Scope (from `SKILL.md`): Dragonfly plus any server-side script — Lua, `EVAL`/`EVALSHA`, `redis.call`, `SCRIPT LOAD`/`LATENCY`, the `eval_*_coordination_total` counters, hashtags, `--lock_on_hashtags`. Typical requests: a script got slower or its p99 degraded; reviewing and rewriting Lua; choosing between Lua, a pipeline, MULTI and a native command; reading the counters and latency output; placing keys for shard-local execution; batching. Not for operating Dragonfly without scripts (deployment, replication, memory) and not for plain Redis. Main thesis: the cost of a script is decided by **where its keys live**, and the same Lua costs about 27 times more per `redis.call` in one mode than in the other.

## 2. Two execution modes, with numbers
The same 256 `redis.call` (HGET, 2048B values, `--proactor_threads=4`, 200 iterations after 20 warm-up), `lab/RESULTS.md` Q5:

| mode | shape | µs/`redis.call` | script p50 | counters per call |
|---|---|---|---|---|
| shard-local | 1 key, atomic | **1.0** | 259.5 µs | shardlocal 1.00 / io 0.00 |
| io-coordinated | 8 keys / 1 hashtag | **26.9** | 6899.0 µs | shardlocal 0.00 / io 1.00 |
| io-coordinated | 8 keys / 8 hashtags | **27.0** | 6912.7 µs | shardlocal 0.00 / io 1.00 |

Ratio ~27x. Key fact: a shared `{hashtag}` **does not by itself put the keys on one shard** with default flags — 8 keys with one tag and 8 keys with eight tags cost the same (26.9 vs 27.0 µs/call). The tag becomes the unit of locking and placement only under `--lock_on_hashtags`.
Tell the modes apart by measurement, not by guessing: `INFO ALL` before and after one call, the difference of `eval_shardlocal_coordination_total` against `eval_io_coordination_total` — exactly one grows by 1 per call (Q5, Q10); `scripts/bench_script.py` prints this split itself. `CONFIG GET lock_on_hashtags` on v1.34.0 returns an empty reply (observed 2026-09-16, lab primary), so a live server's flags are read from the process arguments. For a single hash (shard-local, Q1) a `redis.call` costs 5.1 µs at N=32 and 0.7 µs at N=8192.

## 3. Four measured findings on a real workload
Workload: production Lua scripts from a private service, scrubbed copies in `dragonfly-scripting-workspace/evals/inputs/` and `lab/variants/`.

**a) Batching in `claim_mailbox_batch`** (Q7): 1024 expired candidates, batch 32, records ~2048B, all 14 keys under the `{inbound}` tag. Per-candidate `HGET` → chunked `HMGET` + deferred payload: **p50 41182.7 → 7813.9 µs (5.3x)**, p99 52222.5 → 17149.5 µs, replies identical field by field (after normalizing two server clock fields), `tx_shard_polls` over 200 calls 663192 → 49792. Q10 measures the same pair back to back: **7.6x** with defaults (54947.5 → 7223.5 µs) and **2.6x** under `--lock_on_hashtags` (2243.0 → 862.2 µs). Take absolute numbers from Q7 and ratios from Q10; do not mix them.

**b) Per-grant `HGET` loops instead of `HMGET`** in `apply_new_mutable_mailbox_page.lua:453` and in another script of the source repository — the same shape defect as in (a): N separate `redis.call` where one multi-argument call is enough. The cost of the shape is measured in Q1 (one hash, 1.2x at N=8192) and Q7 (many keys, 5.3x); `lab/RESULTS.md` has no separate measurement of these two lines.

**c) A 32 MiB JSON replay plan on the success path** (Q4, 50 iterations after 3 warm-up, `--maxmemory=2048Mi`): `cjson.encode`+`HSET` costs p50 1904.6 µs at 1 MiB and 15069.9 µs at 8 MiB. At 32 MiB the **first** encode+HSET succeeded (33766783 bytes), and the repeat failed with `-ERR Out of memory`: overwriting a 32 MiB field needs the old and the new blob in memory at once. A concurrent `GET` stayed sub-millisecond here (p99 375.1 µs at 32 MiB against 155.1 µs idle), but the size of the blocking cannot be read from this table — the controlled head-of-line measurement is Q8.

**d) The `--lock_on_hashtags` trade-off** (Q5, Q10, Q11d): the flag turns a single-tag script shard-local (counters 1.00/0.00) and gives a large absolute gain — Q7 orig 54947.5 → 2243.0 µs, Q3 74627.4 → 1555.9 µs, Q2 3936.3 → 214.4 µs. The price is the return of head-of-line blocking: under 4 loaders, `EVALSHA` of a trivial 1-key script that does not touch their keys costs **p50 7712.4 µs** on the node with the flag against **81.2 µs** on the default one (95x), although idle both serve it in 115–125 µs. `SCRIPT LOAD` of a fresh 8894B text: 499 → 478 µs with defaults under load, 440 → 7787 µs under the flag. The flag also cuts the batching gain: 9.9x → 2.0x (Q2), 7.6x → 2.6x (Q7).

## 4. Evidence that the agent actually uses the skill
`usage.json` exists only for the `with_skill` arms; the baseline has none by construction.

| run | read from the skill | ran | server interactions |
|---|---|---|---|
| it-1 eval-1 | SKILL.md, execution-model.md, lua-patterns.md | `bench_script.py`, `lab.sh` | lab started; the report shows `eval_io_coordination_total +1.00 / eval_shardlocal +0.00` per call |
| it-1 eval-2 | SKILL.md, execution-model.md, lua-patterns.md | — | 0; REVIEW.md cites `[lab Q3,Q4,Q5,Q7,Q9,Q10][S1]` |
| it-1 eval-3 | SKILL.md, lua-patterns.md, measurements.md | `lab.sh`, the seed example as a template | 33 docker/redis-cli calls, own container started and removed |
| it-2 eval-1 | SKILL.md, measurements.md, lua-patterns.md, `bench_script.py`, spec+seed from `assets/examples/` | `bench_script.py --spec ... --seed ... --reseed --compare` | **5400** |
| it-2 eval-2 | SKILL.md | `lua_call_audit.py` on the target .lua | 0 |
| it-2 eval-3 | SKILL.md, lua-patterns.md, measurements.md | `lab.sh`, the seed example as a template | 33, container `eval-3-ws-df` started and removed |

Notably: in eval-3 the agent found a discrepancy with `references/measurements.md` (`SCRIPT FLUSH` does not reset `SCRIPT LATENCY` on v1.34.0) — so it read the reference rather than paraphrasing it.

## 5. Evidence that the skill helps
**Iteration 1** (`iteration-1/benchmark.md`, `ANALYSIS.md`): with the skill **95.3% (22/23)** against **73.9% (17/23)**. Per eval: 6/7 vs 6/7, 8/8 vs 6/8, 8/8 vs 5/8.
**Iteration 2** (`iteration-2/benchmark.md`, `ANALYSIS.md`): **25/25 = 100%** against **17/25 = 68%**, delta **+0.32**. Per eval: 8/8 vs 6/8, 9/9 vs 6/9, 8/8 vs 5/8 (eval 3 is a copy of the iteration 1 result).
Latency re-measured by the grader (`iteration-2/ANALYSIS.md`; one fresh v1.34.0 container, `--reseed --compare`, 30 iterations + 5 warm-up):

| arm | original p50 / p99 | optimized p50 / p99 |
|---|---|---|
| with_skill | 99270.6 / 153354.5 µs | 7446.3 / 10883.2 µs |
| without_skill | 68480.6 / 71497.2 µs | 6749.9 / 11164.3 µs |
| control original-vs-original | p50 ratio = 1.000 | — |

The arms were measured **in different runs**, and the original drifts between 68 and 99 ms, so only the before/after ratio **within each arm** is meaningful, not a comparison between arms. Both optimized scripts turned out reply-identical.
**Tokens and time.** Iteration 2: 59773 ± 8694 tokens with the skill against 48053 ± 12443 without; iteration 1: 66320 ± 14039 against 51004 ± 13067. Run durations exist only for iteration 2: eval 1 — 445 s against 435 s, eval 2 — 320 s against 289 s; they were taken from agent completion notifications, not measured in isolation by wall clock.
**Caveats (`iteration-2/ANALYSIS.md`).** In eval 1 two discriminating criteria check reporting discipline, not result quality. In eval 2 the baseline review relied on a false premise — co-location by hashtag with default flags.

## 6. Trigger (description) optimization
From the trigger optimization run (log not kept): 20 trigger queries, 12 train / 8 test (holdout = 0.4).

| point | accuracy (held-out) | recall | precision |
|---|---|---|---|
| starting description (line 20) | 50% | 8% | 50% |
| intermediate (line 50) | 58% | 17% | 100% |
| intermediate (line 80) | 50% | 8% | 50% |
| best loop iteration (line 110) | **62%** | **25%** | **100%** |

On train the best iteration gave 61% accuracy / 22% recall (line 97). The loop **failed at iteration 5/5 on a rate limit** (`RuntimeError: claude -p exited 1` from `improve_description.py`), so this phase did not apply `best_description`.
Final description: rewritten by hand after review under the skill-creator rules — only "when to read" conditions and exclusions, no creation history and no content summary (83 words, folded YAML `>-`). The loop variant (iteration 4, held-out 62%/recall 25%) and two manual versions (logs not kept) scored 10/20: every should-not-trigger passed, every should-trigger failed. The `claude -p` harness counts a trigger only when the FIRST tool call is Skill/Read of the temporary command `dragonfly-scripting-skill-<id>`, so its numbers are understated and do not distinguish the variants; when the skill path was given explicitly (as in the evals) it was read in 100% of runs.

## 6a. Quality review under skill-creator
Independent review (record not kept): PASS WITH FIXES — 2 blockers (stale `--help` in `script_latency.py`, links to `lab/` outside the skill) plus duplicates and narrative about where numbers came from; all fixed in the fix round (the skill is self-contained: the `.lua` variants are copied into `assets/examples/`, `SKILL.md` 161 lines, references carry a "read when" condition).

## 7. Sources
`SOURCES.md` — 93 lines by tier: **A** (official documentation) 30, **B** (source code, design docs, release notes, maintainer answers in repositories with 500+ stars) 39, **C** (vendor engineering blog) 23, **D** (peer-reviewed paper) 1; 6 entries rejected. Evidence rule: a rule enters the skill only with Dragonfly confirmation — a `lab/RESULTS.md Qn` measurement or a link to v1.34.0 documentation or source; Redis-only sources may explain Lua semantics but never justify a performance rule.

## 8. How to reproduce
**Lab.** `lab/run_all.sh` runs `docker compose up -d --wait` (the `single` profile on :6381 when needed), runs every `lab/bench/q*.py` through `lab/.venv/bin/python` and regenerates `RESULTS.md`. Prerequisite: `python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt` in `lab/`. The script does **not** stop the lab — teardown is separate: `docker compose -f lab/compose.yaml down -v`.
**Evals.** Prompts — `dragonfly-scripting-workspace/evals/evals.json`, per-eval metadata — `iteration-2/eval-*/eval_metadata.json`. Aggregation: `python3 -m scripts.aggregate_benchmark <workspace>/iteration-2 --skill-name dragonfly-scripting`, viewing — `python3 eval-viewer/generate_review.py ...`; both from `~/.claude/plugins/cache/claude-plugins-official/skill-creator/b5439c41ae98/skills/skill-creator/`.
**Any before/after claim** — only through `dragonfly-scripting/scripts/bench_script.py --spec ... --seed ... --reseed --compare <a.lua> <b.lua>` on an identical seeded state, with a reply-equality check and with p50 **and** p99.

## 9. Not done
- The description optimization loop did not finish (rate limit at iteration 5/5); the new description was not applied.
- The skill is not symlinked into `~/.claude/skills` / `~/.codex/skills` — that step belongs to the main session.
- Eval 3 was not rerun in iteration 2; its iteration 1 result is counted.
- One run per arm per eval: within-eval variance is not measured.
- Eval 1 checks only the script replies, not the post-state; `--compare` is the contract.
- Checklist item 11 in `dragonfly-scripting/SKILL.md` cites only `[lab Q6][S1]`, without a `[df-doc]` line.
