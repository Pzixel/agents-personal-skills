#!/usr/bin/env python
"""Q8 - head-of-line blocking: 8 concurrent clients run a ~2 ms script in a loop
while a 9th client measures plain `GET` latency.

Four cells: `--proactor_threads=1` (dfskill-single :6381) vs `=4`
(dfskill-primary :6379), probe key in the SAME hashtag as the script's keys vs a
DIFFERENT one. Plus an unloaded baseline per node.

The 8 loaders are separate PROCESSES, not threads, so the probe client's
timings cannot be distorted by the driver's GIL. The script's call count is
calibrated per node to land near 2 ms.
"""
import multiprocessing as mp
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common

QUESTION = "q8"
LOADERS = 8
TARGET_US = 2000
PROBES = 300
WARMUP = 30
LOAD_SECONDS = 14.0
SCRIPT_TAG = "{q8s}"
SAME = SCRIPT_TAG + "probe"
DIFF = "{q8p}probe"
SCRIPT_KEYS = [f"{SCRIPT_TAG}k{i}" for i in range(8)]
VALUE_BYTES = 2048

BODY = """
local n = tonumber(ARGV[1])
local last
for i = 1, n do
  last = redis.call('HGET', KEYS[(i - 1) % #KEYS + 1], 'f')
end
return #last
"""


def loader(port, sha, keys, ncalls, deadline):
    r = common.connect(port)
    while time.time() < deadline:
        try:
            common.evalsha(r, sha, keys, [ncalls])
        except Exception:
            return


def calibrate(r, sha, log, label):
    """Pick the call count that puts one script invocation near TARGET_US.

    Two-point fit: a single ratio would be wrong because the measured time is
    `fixed round-trip + n * per-call`, and on the single-shard node the fixed
    part dominates at small n.
    """
    def t(n):
        return common.percentiles(common.time_calls(
            lambda: common.evalsha(r, sha, SCRIPT_KEYS, [n]), 30, 10))["p50"]

    n1, n2 = 64, 1024
    t1, t2 = t(n1), t(n2)
    slope = (t2 - t1) / (n2 - n1)
    fixed = t1 - n1 * slope
    ncalls = max(1, int((TARGET_US - fixed) / slope))
    got = t(ncalls)
    log.w(f"[{label}] calibration: {n1} calls -> {t1:.0f}us, {n2} calls -> "
          f"{t2:.0f}us => {slope:.3f}us/call + {fixed:.0f}us fixed; chose "
          f"n={ncalls} -> p50 {got:.0f}us (target {TARGET_US}us)")
    return ncalls, got


def probe(r, key):
    return common.percentiles(common.time_calls(lambda: r.get(key), PROBES, WARMUP))


def run_node(port, node, log):
    r = common.connect(port)
    log.section(f"{node} (:{port})")
    log.w(f"server version: {common.server_version(r)}")
    log.w(f"server flags:   {common.server_flags(r)}")
    val = common.payload(VALUE_BYTES, "q8v")
    for k in SCRIPT_KEYS:
        r.delete(k)
        r.hset(k, "f", val)
    r.set(SAME, b"probe-value")
    r.set(DIFF, b"probe-value")
    sha = common.load_script(r, BODY)
    log.cmd(f"SCRIPT LOAD <N x HGET over 8 keys in {SCRIPT_TAG}> -> {sha}")
    ncalls, script_us = calibrate(r, sha, log, node)
    log.cmd(f"EVALSHA {sha} 8 {' '.join(SCRIPT_KEYS)} {ncalls}   # x{LOADERS} loader "
            f"processes in a loop")

    rows = []
    for label, key in ((f"baseline, no load, probe {SAME}", SAME),
                       (f"baseline, no load, probe {DIFF}", DIFF)):
        log.cmd(f"GET {key}   # {PROBES} probes, no load")
        rows.append(common.stat_row(f"{node} | {label}", probe(r, key),
                                    [f"{script_us:.0f}", ncalls]))

    for label, key in ((f"8 loaders, probe SAME hashtag ({SAME})", SAME),
                       (f"8 loaders, probe DIFFERENT hashtag ({DIFF})", DIFF)):
        before = common.script_counters(r)
        deadline = time.time() + LOAD_SECONDS
        procs = [mp.Process(target=loader,
                            args=(port, sha, SCRIPT_KEYS, ncalls, deadline))
                 for _ in range(LOADERS)]
        for p in procs:
            p.start()
        time.sleep(1.0)  # let the loaders saturate before probing
        log.cmd(f"GET {key}   # {PROBES} probes while {LOADERS} loaders run the "
                f"{script_us:.0f}us script")
        st = probe(r, key)
        for p in procs:
            p.join()
        delta = common.counter_delta(before, common.script_counters(r))
        log.w(f"    [{node} | {label}] counters during the loaded window: "
              f"{common.format_counters(delta)}")
        rows.append(common.stat_row(f"{node} | {label}", st,
                                    [f"{script_us:.0f}", ncalls]))
    lat = common.parse_script_latency(common.script_latency_text(r), sha)
    log.w(f"[{node}] {common.format_script_latency(lat)}")
    log.w(lat.get("raw", "(no histogram)"))
    return rows


def run() -> None:
    log = common.RawLog(QUESTION, common.primary(),
                        note=f"{LOADERS} loader PROCESSES running a ~{TARGET_US}us "
                             f"script in a loop; 9th client measures {PROBES} plain "
                             f"GETs; proactor_threads 1 vs 4; probe key in the same "
                             f"hashtag as the script keys vs a different one")
    rows = []
    rows += run_node(common.SINGLE_PORT, "threads=1", log)
    rows += run_node(common.PRIMARY_PORT, "threads=4", log)
    table = common.format_table(
        common.STAT_HEADERS + ["script_us(p50)", "calls/script"], rows)
    log.section("GET probe latency")
    log.w(table)
    log.save()

    cells = {r[0].split(" | ")[0] + ("|same" if "SAME" in r[0] else "|diff"): r
             for r in rows if "loaders" in r[0]}
    c = {k: cells[k] for k in ("threads=1|same", "threads=1|diff",
                               "threads=4|same", "threads=4|diff")}
    body = (
        f"{PROBES} probe `GET`s after {WARMUP} warm-up, issued by a 9th client while "
        f"{LOADERS} separate loader PROCESSES run the ~{TARGET_US}us script in a "
        f"tight loop (processes, not threads, so the driver's GIL cannot distort "
        f"the probe). The script does N x `HGET` over 8 keys in `{SCRIPT_TAG}`; N is "
        f"calibrated per node to land near {TARGET_US}us, see the raw log.\n\n"
        f"{table}\n\n"
        f"The four loaded cells, probe `GET` p99: "
        f"threads=1 same-hashtag **{c['threads=1|same'][4]:.0f}us**, threads=1 "
        f"different-hashtag **{c['threads=1|diff'][4]:.0f}us**, threads=4 "
        f"same-hashtag **{c['threads=4|same'][4]:.0f}us**, threads=4 "
        f"different-hashtag **{c['threads=4|diff'][4]:.0f}us** "
        f"(p50 {c['threads=1|same'][2]:.0f} / {c['threads=1|diff'][2]:.0f} / "
        f"{c['threads=4|same'][2]:.0f} / {c['threads=4|diff'][2]:.0f}us).\n\n"
        f"Read the same/different-hashtag rows with Q5 in mind: on default flags "
        f"(no `--lock_on_hashtags`) a hashtag does NOT pin keys to one shard, so "
        f"the two probe keys differ only by which shard their own hash lands on, "
        f"not by membership of the script's shard.\n")
    common.emit(QUESTION, "head-of-line blocking (8 loaders x ~2ms script, 9th "
                          "client GET)",
                ["Q8", f"{LOADERS} clients x ~{TARGET_US}us script, 9th client GET p99, "
                       f"threads 1 vs 4, probe in same vs different hashtag",
                 f"p99 t1/same {c['threads=1|same'][4]:.0f}us, t1/diff "
                 f"{c['threads=1|diff'][4]:.0f}us, t4/same {c['threads=4|same'][4]:.0f}us, "
                 f"t4/diff {c['threads=4|diff'][4]:.0f}us",
                 "see the four cells in the appendix"],
                body)


if __name__ == "__main__":
    mp.set_start_method("spawn")
    run()
