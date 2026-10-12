#!/usr/bin/env python
"""Q10 - how much of each measured optimization win survives under
`--lock_on_hashtags`?

Q5/B established that on DEFAULT flags a shared hashtag does NOT co-locate keys:
an 8-key script in one hashtag is io-coordinated (~27us per `redis.call`) exactly
like 8 keys in 8 hashtags, while a 1-key script is shard-local (~1.1us/call).
Only `--lock_on_hashtags` puts a one-hashtag multi-key script on the shard-local
path. Every optimization measured so far (Q2 write batching, Q3 read-after-full,
Q7 the real `claim_mailbox_batch.lua`) mainly removes `redis.call` crossings, so
if the per-call cost drops ~27x the win may largely evaporate - which would change
the advice the skill gives.

So this benchmark re-runs the Q2, Q3 and Q7 workloads VERBATIM (same scripts, same
seeding code, imported from those modules) on two servers back to back in one pass:
the default lab primary :6379 and a throwaway `--lock_on_hashtags` node :6382 with
otherwise identical production flags (via `common.start_flag_node`, the same
mechanism Q5 uses). Back to back matters: B found Q7 strongly state-dependent
(41.2ms vs 67.1ms for the same script depending on prior load), so numbers from
different sessions are not comparable.

Every cell reports the client percentiles AND the INFO execution-path counter
delta, so which path each cell took is visible rather than inferred.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common
import q2_write_batching as q2
import q3_read_after_full as q3
import q7_real_script as q7

QUESTION = "q10"
ITERS = 200
WARMUP = 20
ITERS7 = 200            # Q7 iterations per cell (stated in the report)
WARMUP7 = 20
NODES = (("default", "primary :6379"), ("lock_on_hashtags", "throwaway :6382"))

# Optional extra: Dragonfly-only async call with a discarded result.
BATCHED_ACALL = q2.BATCHED.replace("redis.call", "redis.acall")

PATH_HEADERS = ["redis_calls", "us_per_call(p50)", "shardlocal/io per invocation"]


def path_of(delta: dict, n: int) -> str:
    return (f"{delta.get('eval_shardlocal_coordination_total', 0) / n:.2f}/"
            f"{delta.get('eval_io_coordination_total', 0) / n:.2f}")


def measure(r, log, label, sha, keys, argv, ncalls, iters=ITERS, warmup=WARMUP):
    """Time one script on one node and take the INFO path counters across exactly
    the timed invocations."""
    log.cmd(f"EVALSHA {sha} {len(keys)} {' '.join(keys)[:160]} "
            f"{' '.join(str(a)[:16] for a in argv)[:80]}")
    before = common.script_counters(r)
    st = common.percentiles(common.time_calls(
        lambda: common.evalsha(r, sha, keys, argv), iters, warmup))
    delta = common.counter_delta(before, common.script_counters(r))
    n = iters + warmup
    log.w(f"    [{label}] counters over {n} invocations: "
          f"{common.format_counters(delta)}")
    row = common.stat_row(label, st, [ncalls, round(st["p50"] / ncalls, 2),
                                      path_of(delta, n)])
    return row, delta


# --------------------------------------------------------------------------
# Q2 -- write batching, verbatim scripts from q2_write_batching
# --------------------------------------------------------------------------

def q2_cell(r, log, node):
    for k in q2.KEYS:
        r.delete(k)
    val = common.payload(q2.VALUE_BYTES, "q2v")
    rows, deltas = [], {}
    variants = [("per-item", q2.PER_CALL, q2.ITEMS * (q2.FIELDS_PER_ITEM + 1),
                 f"{q2.ITEMS}x({q2.FIELDS_PER_ITEM} HSET + 1 ZADD)"),
                ("batched", q2.BATCHED, q2.FIELDS_PER_ITEM + 1,
                 f"{q2.FIELDS_PER_ITEM} multi-field HSET + 1 multi-member ZADD")]
    try:
        r.eval(BATCHED_ACALL, len(q2.KEYS), *q2.KEYS, q2.ITEMS, val)
        variants.append(("batched/acall", BATCHED_ACALL, q2.FIELDS_PER_ITEM + 1,
                         "batched, redis.acall (result discarded)"))
    except Exception as exc:                       # acall unsupported on this build
        log.w(f"    [{node}] redis.acall unavailable: {exc}")
    for key, body, ncalls, name in variants:
        sha = common.load_script(r, body)
        log.cmd(f"SCRIPT LOAD <q2 {key}> -> {sha}")
        common.evalsha(r, sha, q2.KEYS, [q2.ITEMS, val])
        assert r.zcard(q2.KEYS[-1]) == q2.ITEMS and r.hlen(q2.KEYS[0]) == q2.ITEMS
        row, delta = measure(r, log, f"[{node}] {name}", sha, q2.KEYS,
                             [q2.ITEMS, val], ncalls)
        rows.append(row)
        deltas[key] = delta
    return rows, deltas


# --------------------------------------------------------------------------
# Q3 -- read-after-full vs early stop, verbatim scripts + seeder from q3
# --------------------------------------------------------------------------

def q3_cell(r, log, node):
    q3.seed(r)
    log.cmd(f"(q3 seed) ZADD {q3.KEYS[0]} 0..{q3.CANDIDATES - 1} ; "
            f"HSET {q3.KEYS[3]} <{q3.PAYLOAD_BYTES}B payloads>")
    rows, deltas = [], {}
    for key, body, args, want_bytes, ncalls, name in (
        ("full", q3.FULL, [q3.CANDIDATES, q3.CANDIDATES],
         q3.CANDIDATES * q3.PAYLOAD_BYTES, q3.CANDIDATES * 3,
         f"payload for all {q3.CANDIDATES}"),
        ("early", q3.EARLY, [q3.CANDIDATES, q3.CANDIDATES, q3.BATCH],
         q3.BATCH * q3.PAYLOAD_BYTES, q3.CANDIDATES * 2 + q3.BATCH,
         f"payload for first {q3.BATCH}"),
    ):
        sha = common.load_script(r, body)
        log.cmd(f"SCRIPT LOAD <q3 {key}> -> {sha}")
        n, chosen, got = common.evalsha(r, sha, q3.KEYS, args)
        assert n == q3.CANDIDATES and got == want_bytes, (node, key, n, chosen, got)
        log.w(f"    [{node}] {key}: candidates={n} chosen={chosen} payload_bytes={got}")
        row, delta = measure(r, log, f"[{node}] {name}", sha, q3.KEYS, args, ncalls)
        rows.append(row)
        deltas[key] = delta
    return rows, deltas


# --------------------------------------------------------------------------
# Q7 -- the real claim_mailbox_batch.lua, orig vs hmget, with the fence and
# reply-equality checks q7 already performs
# --------------------------------------------------------------------------

def q7_cell(r, log, node, bodies):
    q7.seed_static(r, log)
    rid = q7.run_id(r)
    shas = {}
    for name, body in bodies.items():
        shas[name] = common.load_script(r, body)
        log.cmd(f"SCRIPT LOAD <q7 {name}> -> {shas[name]}")

    fixed = q7.tokens()
    norms = {}
    for name in ("orig", "hmget"):
        q7.reset_mutable(r)
        reply = common.evalsha(r, shas[name], q7.K, q7.argv(fixed, rid))
        now, entries = q7.check_reply(reply, fixed, log, f"{node}/{name}")
        norms[name] = q7.normalized(reply, now, entries)
    identical = norms["orig"] == norms["hmget"]
    log.w(f"    [{node}] reply arrays identical (clock fields normalised): {identical}")

    rows, deltas = [], {}
    for name, label in (("orig", "orig (per-candidate HGET)"),
                        ("hmget", "hmget prefetch + deferred payload")):
        samples, delta = q7.timed(r, shas[name], rid, log, f"{node}/{name}")
        deltas[name] = delta
        st = common.percentiles(samples)
        rows.append(common.stat_row(f"[{node}] {label}", st,
                                    [path_of(delta, ITERS7)]))
    return rows, deltas, identical


def run() -> None:
    rp = common.primary()
    log = common.RawLog(QUESTION, rp,
                        note="Q2/Q3/Q7 workloads re-run verbatim on the default "
                             "primary and on a throwaway --lock_on_hashtags node, "
                             "back to back in one session")
    log.section("throwaway node")
    log.cmd(f"docker run -d --name {common.FLAG_NAME} -p {common.FLAG_PORT}:6379 "
            f"{common.IMAGE} {' '.join(common.BASE_ARGS)} --lock_on_hashtags")
    rl = common.start_flag_node(["--lock_on_hashtags"])
    log.w(f"flag node version: {common.server_version(rl)}")
    log.w(f"flag node flags:   {' '.join(common.BASE_ARGS)} --lock_on_hashtags")
    clients = {"default": rp, "lock_on_hashtags": rl}

    tables = {}
    for q, cell in (("q2", q2_cell), ("q3", q3_cell)):
        rows, deltas, per_node = [], {}, {}
        for node, _ in NODES:
            log.section(f"{q.upper()} workload on {node}")
            rws, dts = cell(clients[node], log, node)
            rows += rws
            per_node[node] = len(rws)
            deltas[node] = dts
        t = common.format_table(common.STAT_HEADERS + PATH_HEADERS, rows)
        log.w(t)
        tables[q] = (t, rows, deltas, per_node)

    bodies = {name: (q7.VARIANTS / fn).read_text() for name, fn in
              (("orig", "claim_mailbox_batch.orig.lua"),
               ("hmget", "claim_mailbox_batch.hmget.lua"))}
    q7.ITERS, q7.WARMUP = ITERS7, WARMUP7
    rows7, deltas7, ident = [], {}, {}
    for node, _ in NODES:
        log.section(f"Q7 workload (claim_mailbox_batch.lua) on {node}")
        rws, dts, ok = q7_cell(clients[node], log, node, bodies)
        rows7 += rws
        deltas7[node] = dts
        ident[node] = ok
    t7 = common.format_table(
        common.STAT_HEADERS + ["shardlocal/io per invocation"], rows7)
    log.w(t7)

    common.stop_flag_node()
    log.cmd(f"docker rm -f {common.FLAG_NAME}   # lab back to its default state")

    def p50(rows, i):
        return rows[i][2]

    q2r, q3r = tables["q2"][1], tables["q3"][1]
    nq2 = tables["q2"][3]["default"]          # 2 or 3 variants depending on acall
    d_q2 = p50(q2r, 0) / p50(q2r, 1)
    l_q2 = p50(q2r, nq2) / p50(q2r, nq2 + 1)
    d_q3 = p50(q3r, 0) / p50(q3r, 1)
    l_q3 = p50(q3r, 2) / p50(q3r, 3)
    d_q7 = p50(rows7, 0) / p50(rows7, 1)
    l_q7 = p50(rows7, 2) / p50(rows7, 3)
    acall = ""
    if nq2 == 3 and tables["q2"][3]["lock_on_hashtags"] == 3:
        acall = (f" `redis.acall` on the batched writes: p50 {p50(q2r, 2):.0f}us "
                 f"(default) / {p50(q2r, 5):.0f}us (lock_on_hashtags) against "
                 f"{p50(q2r, 1):.0f}us / {p50(q2r, nq2 + 1):.0f}us for sync "
                 f"`redis.call`.")

    body = (
        f"Both nodes measured back to back in ONE session (B found Q7 strongly "
        f"state-dependent, so cross-session comparison is unsound). Default node is "
        f"the lab primary :6379; the `--lock_on_hashtags` node is a throwaway "
        f"container on :6382 with the same production flags "
        f"(`{' '.join(common.BASE_ARGS)}`), removed at the end of the run. Scripts, "
        f"seeding and fences are imported verbatim from `q2_write_batching.py`, "
        f"`q3_read_after_full.py` and `q7_real_script.py`. "
        f"`shardlocal/io per invocation` is the INFO "
        f"`eval_shardlocal_coordination_total`/`eval_io_coordination_total` delta "
        f"over the timed invocations divided by their count.\n\n"
        f"**Q2 - write batching** ({ITERS} iterations after {WARMUP} warm-up)\n\n"
        f"{tables['q2'][0]}\n\n"
        f"Batching wins **{d_q2:.1f}x** on the default node and **{l_q2:.1f}x** "
        f"under `--lock_on_hashtags`.{acall}\n\n"
        f"**Q3 - read-after-full vs first {q3.BATCH} payloads** ({ITERS} iterations "
        f"after {WARMUP} warm-up)\n\n{tables['q3'][0]}\n\n"
        f"Skipping the {q3.CANDIDATES - q3.BATCH} payload `HGET`s wins "
        f"**{d_q3:.1f}x** on the default node and **{l_q3:.1f}x** under "
        f"`--lock_on_hashtags`.\n\n"
        f"**Q7 - `claim_mailbox_batch.lua`, 4 cells** ({ITERS7} iterations per cell "
        f"after {WARMUP7} warm-up; mutable state reset before every invocation, "
        f"reset not timed)\n\n{t7}\n\n"
        f"Every timed cell was fence-checked `RESOLVED` with "
        f"`{q7.BATCH_SIZE}/{q7.BATCH_SIZE}` `CLAIMED` and `deadline - now == "
        f"{q7.LEASE_TTL}` before being trusted. Reply arrays identical field for "
        f"field (clock fields normalised): default "
        f"**{ident['default']}**, lock_on_hashtags **{ident['lock_on_hashtags']}**. "
        f"The HMGET variant wins **{d_q7:.1f}x** on the default node and "
        f"**{l_q7:.1f}x** under `--lock_on_hashtags`.\n\n"
        f"**Counter split.** Q7 orig: default "
        f"`{common.format_counters(deltas7['default']['orig'])}`, lock_on_hashtags "
        f"`{common.format_counters(deltas7['lock_on_hashtags']['orig'])}`. Q7 hmget: "
        f"default `{common.format_counters(deltas7['default']['hmget'])}`, "
        f"lock_on_hashtags "
        f"`{common.format_counters(deltas7['lock_on_hashtags']['hmget'])}`. Q2 "
        f"per-item: default "
        f"`{common.format_counters(tables['q2'][2]['default']['per-item'])}`, "
        f"lock_on_hashtags "
        f"`{common.format_counters(tables['q2'][2]['lock_on_hashtags']['per-item'])}`. "
        f"Q3 full: default "
        f"`{common.format_counters(tables['q3'][2]['default']['full'])}`, "
        f"lock_on_hashtags "
        f"`{common.format_counters(tables['q3'][2]['lock_on_hashtags']['full'])}`.\n\n"
        f"**Reconciliation with the standalone Q2/Q3/Q7 runs.** The same three "
        f"workloads were measured twice on default flags and the two measurements "
        f"disagree: Q2 p50 5845.8us standalone vs 3936.3us in this section's "
        f"default arm, Q3 34135.8 vs 74627.4us (2.2x apart), Q7 41182.7 vs "
        f"54947.5us -- so the derived speedups differ too (Q2 10.8x / 9.9x, Q3 "
        f"1.1x / 1.3x, Q7 5.3x / 7.6x; standalone figures as recorded in the Q2, "
        f"Q3 and Q7 sections of this report). These workloads are load-sensitive "
        f"and the lab host has 4 cores: the raw log shows the throwaway "
        f"`--lock_on_hashtags` container (4 busy-polling proactors) was already "
        f"running when this section's default arm executed, so the default arm "
        f"carries a co-tenant the standalone runs did not have. Take the RATIOS "
        f"from this section -- its two arms were measured back to back under the "
        f"same co-tenancy -- and the ABSOLUTE numbers from the standalone Q2/Q3/Q7 "
        f"runs. Do not mix the two.\n\n"
        f"All 14 `claim_mailbox_batch` keys share the hashtag `{q7.TAG}`, so this is "
        f"exactly the shape `--lock_on_hashtags` converts from io-coordinated to "
        f"shard-local; the Q2 and Q3 key sets likewise share one hashtag each.\n")

    log.save()
    common.emit(QUESTION,
                "how much of each win survives under `--lock_on_hashtags`",
                ["Q10", "Q2/Q3/Q7 workloads re-run on default vs "
                        "`--lock_on_hashtags`, back to back",
                 f"batching {d_q2:.1f}x -> {l_q2:.1f}x; payload skip "
                 f"{d_q3:.1f}x -> {l_q3:.1f}x; claim_mailbox_batch hmget "
                 f"{d_q7:.1f}x -> {l_q7:.1f}x",
                 "see the 4-cell Q7 table and the counter split in the appendix; "
                 "the flag's cost under concurrent load is in Q11(d)"],
                body)


if __name__ == "__main__":
    run()
