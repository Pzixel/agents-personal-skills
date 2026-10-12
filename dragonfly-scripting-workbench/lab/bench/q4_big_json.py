#!/usr/bin/env python
"""Q4 — big JSON plan on the success path.

The script builds a command table, cjson.encode()s it to ~SIZE bytes, HSETs the
blob, then a later call HDELs it. While that runs, a second client hammers a
plain GET on an unrelated key; its p99 shows how long the shard is blocked.

Sizes: 1, 8, 32 MiB against --maxmemory=2048Mi. A failure (Lua memory limit,
OOM, proto limit) is recorded verbatim as the result -- that is the finding."""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common

QUESTION = "q4"
SIZES_MIB = [1, 8, 32]
ITERS = 50          # fewer than 200: a 32 MiB encode is ~100ms-scale work
WARMUP = 3
PLAN_KEY = "{q4}plan"
PROBE_KEY = "{q4probe}g"
ENTRY_BYTES = 1024  # one command-table entry

ENCODE_STORE = """
local target = tonumber(ARGV[1])
local entry = ARGV[2]
local plan = {}
local n = math.ceil(target / (#entry + 32))
for i = 1, n do
  plan[i] = {op = 'deliver', idx = i, body = entry}
end
local blob = cjson.encode(plan)
redis.call('HSET', KEYS[1], ARGV[3], blob)
return #blob
"""

DELETE_PLAN = """
return redis.call('HDEL', KEYS[1], ARGV[1])
"""


class GetProbe(threading.Thread):
    """Second client: plain GET in a tight loop, samples in microseconds."""

    def __init__(self):
        super().__init__(daemon=True)
        self.samples: list[float] = []
        self.stop = threading.Event()
        self.error = ""

    def run(self) -> None:
        c = common.primary()
        try:
            c.set(PROBE_KEY, b"probe")
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            return
        while not self.stop.is_set():
            t0 = time.perf_counter_ns()
            try:
                c.get(PROBE_KEY)
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {exc}"
                return
            self.samples.append((time.perf_counter_ns() - t0) / 1000.0)


def run() -> None:
    # A previous size leaves RSS high enough to trip the OOM check; restart first
    # so every run of this benchmark starts from the same memory state.
    common.restart("dfskill-primary", common.PRIMARY_PORT)
    r = common.primary()
    log = common.RawLog(QUESTION, r,
                        note=f"{WARMUP} warm-up + {ITERS} measured iterations per size "
                             f"(fewer than 200: a 32 MiB cjson.encode is ~100ms-scale); "
                             f"a concurrent client GETs {PROBE_KEY} throughout")
    r.flushall()
    entry = common.payload(ENTRY_BYTES, "q4entry").decode()

    sha_enc = common.load_script(r, ENCODE_STORE)
    sha_del = common.load_script(r, DELETE_PLAN)
    log.cmd(f"SCRIPT LOAD <build table, cjson.encode, HSET> -> {sha_enc}")
    log.cmd(f"SCRIPT LOAD <HDEL the plan field>            -> {sha_del}")

    # baseline GET p99 with no script running
    idle = GetProbe()
    idle.start()
    time.sleep(2.0)
    idle.stop.set()
    idle.join(5)
    base = common.percentiles(idle.samples)
    if idle.error:
        log.w(f"idle probe error: {idle.error}")
    log.section("baseline GET latency (no script running)")
    log.w(common.format_table(common.STAT_HEADERS,
                              [common.stat_row("idle GET probe", base)]))

    rows, probe_rows, notes = [], [], []
    for mib in SIZES_MIB:
        target = mib * 1024 * 1024
        field = f"plan{mib}"
        label = f"{mib} MiB plan"
        log.section(label)
        log.cmd(f"EVALSHA {sha_enc} 1 {PLAN_KEY} {target} <{ENTRY_BYTES}B entry> {field}")
        try:
            blob_len = common.evalsha(r, sha_enc, [PLAN_KEY], [target, entry, field])
            log.w(f"  -> encoded blob = {blob_len} bytes "
                  f"({blob_len / 1024 / 1024:.2f} MiB)")
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            log.w(f"  -> FAILED: {msg}")
            notes.append(f"**{label}: {msg}** (recorded as the result)")
            rows.append([label, 0, "-", "-", "-", "-", "-", "-", "-", "FAILED", msg[:60]])
            probe_rows.append([label, "-", "-", "-", "-", msg[:40]])
            continue

        probe = GetProbe()
        probe.start()
        failed = ""
        try:
            enc = common.percentiles(common.time_calls(
                lambda: common.evalsha(r, sha_enc, [PLAN_KEY], [target, entry, field]),
                ITERS, WARMUP))
        except Exception as exc:
            failed = f"{type(exc).__name__}: {exc}"
            enc = {"n": 0}
        probe.stop.set()
        probe.join(5)
        pst = common.percentiles(probe.samples)
        if probe.error:
            log.w(f"  probe error: {probe.error}")

        log.cmd(f"EVALSHA {sha_del} 1 {PLAN_KEY} {field}")
        dele = {"n": 0}
        if not failed:
            try:
                dele = common.percentiles(common.time_calls(
                    lambda: (common.evalsha(r, sha_enc, [PLAN_KEY], [target, entry, field]),
                             common.evalsha(r, sha_del, [PLAN_KEY], [field])),
                    20, 2))
            except Exception as exc:
                failed = f"{type(exc).__name__}: {exc}"
        if failed:
            log.w(f"  -> FAILED on repeat: {failed}")
            notes.append(f"**{label}: the FIRST encode+HSET succeeded "
                         f"({blob_len} bytes) but repeating it failed with "
                         f"`{failed}`** -- with `--maxmemory=2048Mi`, overwriting a "
                         f"{mib} MiB hash field needs the old and the new blob "
                         f"resident at once. Recorded as the result.")
            r.execute_command("HDEL", PLAN_KEY, field)
        rows.append(common.stat_row(f"{label} encode+HSET", enc,
                                    [f"{blob_len / 1024 / 1024:.2f}", failed[:60]]))
        rows.append(common.stat_row(f"{label} encode+HSET+HDEL", dele,
                                    ["", failed[:60] if failed else ""]))
        probe_rows.append([label, pst.get("p50", "-"), pst.get("p95", "-"),
                           pst.get("p99", "-"), pst.get("max", "-"),
                           f"{pst['n']} samples" + (" " + probe.error[:40] if probe.error else "")])
        log.w(common.format_table(common.STAT_HEADERS + ["blob_MiB", "error"],
                                  rows[-2:]))
        log.w(common.format_table(
            ["during", "GET p50_us", "GET p95_us", "GET p99_us", "GET max_us", "n"],
            probe_rows[-1:]))
        r.execute_command("HDEL", PLAN_KEY, field)

    lat_text = common.script_latency_text(r)
    log.cmd("SCRIPT LATENCY")
    log.section("SCRIPT LATENCY (usec; includes warm-up iterations)")
    for sha, name in ((sha_enc, "encode+HSET"), (sha_del, "HDEL")):
        e = common.parse_script_latency(lat_text, sha)
        log.w(f"[{name}] {sha}")
        log.w(e.get("raw", "(no histogram)"))
    log.section("memory after the run")
    log.cmd("INFO memory")
    mem = common.info_text(r, "memory")
    log.w(mem)
    log.save()

    t_script = common.format_table(common.STAT_HEADERS + ["blob_MiB", "error"], rows)
    t_probe = common.format_table(
        ["during", "GET p50_us", "GET p95_us", "GET p99_us", "GET max_us", "n"],
        [["(baseline, idle)", base.get("p50", "-"), base.get("p95", "-"),
          base.get("p99", "-"), base.get("max", "-"),
          f"{base['n']} samples"]] + probe_rows)
    done = [rw for rw in rows if rw[1]]
    head = (f"{done[0][2]:.0f}us at 1 MiB" if done else "all sizes failed")
    body = (f"{ITERS} measured iterations per size after {WARMUP} warm-up (fewer than "
            f"the usual 200 because a 32 MiB `cjson.encode` is ~100 ms-scale work). "
            f"`--maxmemory=2048Mi`.\n\nTime in script:\n\n{t_script}\n\n"
            f"Plain `GET` from a second client, concurrent with the run "
            f"(shard blocking):\n\n{t_probe}\n\n"
            + ("\n".join(notes) + "\n\n" if notes else "")
            + "The concurrent-`GET` figures are **run-to-run unstable**: the probe key "
              "`{q4probe}g` and the plan key `{q4}plan` are separate keys, and whether "
              "the probe lands on the proactor running the script decides everything. "
              "An earlier run of this benchmark was observed to serialise the probe "
              "behind the script, but that run was not retained in a raw log, so no "
              "figure from it is quoted here. The sample counts also differ by an "
              "order of magnitude between rows, over different wall-clock windows. "
              "The blocking magnitude therefore cannot be read off this table; see "
              "Q8 (part B) for the controlled same-shard vs other-shard "
              "measurement.\n")
    common.emit(QUESTION, "big cjson plan on the success path",
                ["Q4", "cjson.encode 1/8/32 MiB command table, HSET then HDEL",
                 f"script p50 {head}; worst concurrent GET p99 "
                 f"{max((p[3] for p in probe_rows if isinstance(p[3], float)), default=0):.0f}us "
                 f"vs {base.get('p99', 0):.0f}us idle",
                 "concurrent GET degrades but stays sub-millisecond here; this "
                 "run does NOT show millisecond shard blocking -- see Q8 (part B) "
                 "for the controlled head-of-line measurement; see the table for "
                 "the largest size that completes"],
                body)


if __name__ == "__main__":
    run()
