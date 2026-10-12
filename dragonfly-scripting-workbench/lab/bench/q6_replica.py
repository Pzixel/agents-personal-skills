#!/usr/bin/env python
"""Q6 - replica attached vs standalone: A's Q2 write script, unmodified.

Both scripts, keys, sizes and iteration counts are imported verbatim from
`q2_write_batching` so the numbers are directly comparable with Q2.

Standalone condition: the SAME primary process, with the replica detached by
sending `REPLICAOF NO ONE` to `dfskill-replica` (:6380) and waiting until the
primary reports `connected_slaves:0`. Nothing about the primary changes -- no
restart, no flag change, same script shas -- so the only difference is whether
the write path also has to feed a replication stream. The replica is
re-attached (`REPLICAOF primary 6379`) and full sync is confirmed before exit.
"""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common
import q2_write_batching as q2

QUESTION = "q6"
ITERS = q2.ITERS
WARMUP = q2.WARMUP


def connected_slaves(r) -> int:
    m = re.search(r"connected_slaves:(\d+)", common.info_text(r, "replication"))
    return int(m.group(1)) if m else -1


def wait_slaves(r, want: int, log, timeout_s: float = 30.0) -> int:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        n = connected_slaves(r)
        if n == want:
            log.w(f"primary connected_slaves:{n}")
            return n
        time.sleep(0.5)
    n = connected_slaves(r)
    log.w(f"!! primary connected_slaves:{n} (wanted {want}) after {timeout_s}s")
    return n


def measure(r, log, label, sha, ncalls):
    log.cmd(f"EVALSHA {sha} {len(q2.KEYS)} {' '.join(q2.KEYS)} {q2.ITEMS} "
            f"<{q2.VALUE_BYTES}B>")
    val = common.payload(q2.VALUE_BYTES, "q2v")
    common.evalsha(r, sha, q2.KEYS, [q2.ITEMS, val])
    before = common.script_counters(r)
    st = common.percentiles(common.time_calls(
        lambda: common.evalsha(r, sha, q2.KEYS, [q2.ITEMS, val]), ITERS, WARMUP))
    delta = common.counter_delta(before, common.script_counters(r))
    log.w(f"    [{label}] counters over {ITERS + WARMUP} invocations: "
          f"{common.format_counters(delta)}")
    return common.stat_row(label, st, [ncalls, round(st["p50"] / ncalls, 2)]), delta


def run() -> None:
    r = common.primary()
    rep = common.replica()
    log = common.RawLog(QUESTION, r,
                        note="A's Q2 write scripts verbatim (32 items x (5 HSET + "
                             "1 ZADD) vs 5 multi-field HSET + 1 multi-member ZADD), "
                             "primary :6379 with the replica attached vs detached")
    for k in q2.KEYS:
        r.delete(k)
    sha_per = common.load_script(r, q2.PER_CALL)
    sha_batch = common.load_script(r, q2.BATCHED)
    log.cmd(f"SCRIPT LOAD <Q2 per-call> -> {sha_per}")
    log.cmd(f"SCRIPT LOAD <Q2 batched>  -> {sha_batch}")
    per_calls = q2.ITEMS * (q2.FIELDS_PER_ITEM + 1)
    batch_calls = q2.FIELDS_PER_ITEM + 1
    variants = [("32x(5 HSET + 1 ZADD)", sha_per, per_calls),
                ("5 multi-HSET + 1 multi-ZADD", sha_batch, batch_calls)]

    def attach(state):
        if state == "attached":
            log.cmd("REPLICAOF primary 6379  # on dfskill-replica :6380")
            rep.execute_command("REPLICAOF", "primary", "6379")
            return wait_slaves(r, 1, log)
        log.cmd("REPLICAOF NO ONE  # on dfskill-replica :6380")
        rep.execute_command("REPLICAOF", "NO", "ONE")
        return wait_slaves(r, 0, log)

    # ABBA order, two replicates per condition: a single A-then-B pair cannot tell
    # a replication cost from drift, and the first pilot run of this benchmark put
    # the two conditions on opposite sides of each other.
    slaves = {}
    cells: dict[tuple, list] = {}
    for round_ix, state in enumerate(("attached", "standalone", "standalone",
                                      "attached")):
        log.section(f"round {round_ix + 1}: {state}")
        slaves[state] = attach(state)
        log.w(common.info_text(r, "replication").strip().splitlines()[0])
        for name, sha, calls in variants:
            row, _ = measure(r, log, f"{name} | {state} #{len(cells.get((name, state), [])) + 1}",
                             sha, calls)
            cells.setdefault((name, state), []).append(row)

    log.section("restoring the lab: replica attached")
    attach("attached")
    log.w(common.info_text(rep, "replication").strip())
    attached_n, standalone_n = slaves["attached"], slaves["standalone"]

    lat = common.script_latency_text(r)
    log.cmd("SCRIPT LATENCY")
    log.section("SCRIPT LATENCY (usec; both conditions share one sha, so these "
                "histograms cover attached+standalone together)")
    for sha, name in ((sha_per, "per-call"), (sha_batch, "batched")):
        log.w(f"[{name}] {sha}")
        log.w(common.parse_script_latency(lat, sha).get("raw", "(no histogram)"))

    rows = [row for name, _, _ in variants for state in ("attached", "standalone")
            for row in cells[(name, state)]]
    def p50(name, state):
        xs = [c[2] for c in cells[(name, state)]]
        return sum(xs) / len(xs), min(xs), max(xs)
    per_att, per_alone = p50(variants[0][0], "attached"), p50(variants[0][0], "standalone")
    bat_att, bat_alone = p50(variants[1][0], "attached"), p50(variants[1][0], "standalone")
    table = common.format_table(
        common.STAT_HEADERS + ["redis_calls", "us_per_call(p50)"], rows)
    log.section("client latency")
    log.w(table)
    log.save()

    pct_per = 100.0 * (per_att[0] - per_alone[0]) / per_alone[0]
    pct_batch = 100.0 * (bat_att[0] - bat_alone[0]) / bat_alone[0]
    # a difference smaller than the spread between the two replicates of the same
    # condition is not a replication cost, it is run-to-run variation
    spread_per = max(per_att[2] - per_att[1], per_alone[2] - per_alone[1])
    spread_bat = max(bat_att[2] - bat_att[1], bat_alone[2] - bat_alone[1])
    verdict_per = "within" if abs(per_att[0] - per_alone[0]) <= spread_per else "beyond"
    verdict_bat = "within" if abs(bat_att[0] - bat_alone[0]) <= spread_bat else "beyond"
    body = (
        f"{ITERS} measured iterations after {WARMUP} warm-up per cell, A's Q2 write "
        f"scripts imported verbatim (`q2_write_batching.PER_CALL` / `.BATCHED`, same "
        f"KEYS, same {q2.VALUE_BYTES}B values).\n\n"
        f"**How the standalone condition was produced:** the same primary process, "
        f"with `REPLICAOF NO ONE` sent to `dfskill-replica` (:6380) and the primary "
        f"confirmed at `connected_slaves:{standalone_n}` (it was "
        f"`connected_slaves:{attached_n}` for condition A). No restart, no flag "
        f"change, identical script shas across both conditions; the replica is "
        f"re-attached at the end and full sync confirmed in the raw log.\n\n"
        f"{table}\n\n"
        f"Two replicates per condition in ABBA order. Mean p50 with the replica "
        f"attached vs standalone: per-call {per_att[0]:.0f}us vs {per_alone[0]:.0f}us "
        f"({pct_per:+.1f}%), batched {bat_att[0]:.0f}us vs {bat_alone[0]:.0f}us "
        f"({pct_batch:+.1f}%). The spread between the two replicates of the SAME "
        f"condition is {spread_per:.0f}us (per-call) and {spread_bat:.0f}us "
        f"(batched), so the attached-vs-standalone difference is **{verdict_per}** "
        f"run-to-run variation for the per-call script and **{verdict_bat}** it for "
        f"the batched one. A positive percentage means the attached replica was "
        f"slower.\n")
    common.emit(QUESTION, "replica attached vs standalone (Q2 write script)",
                ["Q6", "Q2 write scripts, primary with replica attached vs detached",
                 f"mean p50 attached vs standalone: per-call {per_att[0]:.0f} vs "
                 f"{per_alone[0]:.0f}us ({pct_per:+.1f}%), batched {bat_att[0]:.0f} vs "
                 f"{bat_alone[0]:.0f}us ({pct_batch:+.1f}%)",
                 f"difference is {verdict_per} (per-call) / {verdict_bat} (batched) "
                 f"the spread between replicates of the same condition"],
                body)


if __name__ == "__main__":
    run()
