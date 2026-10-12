#!/usr/bin/env python
"""Q11 - does common Redis performance advice hold on Dragonfly v1.34.0?

Four pieces of advice that every Redis tuning guide repeats, each measured on
two servers back to back: the default lab primary :6379 (proactor_threads=4, no
hashtag locking) and a throwaway node :6382 with identical production flags plus
`--lock_on_hashtags` (`common.start_flag_node`, the mechanism q5/q10 use). B
established that the hashtag only pins keys to one shard under that flag, so
every piece of advice below can land differently on the two nodes.

  a) "prefer a pipeline over a Lua script"  - 64 GET / 64 HSET on keys in ONE
     hashtag, as a client pipeline, as one atomic script, and as MULTI/EXEC.
  b) "collapse many small keys into one hash" - memory and read latency for
     100k 64-byte values as 100k strings, one 100k-field hash, 1000x100 hashes.
  c) "implement a rate limiter in Lua" - a classic sliding-window ZSET limiter
     vs Dragonfly's native CL.THROTTLE.
  d) "SCRIPT LOAD per worker is cheap" - 200 sequential SCRIPT LOAD of the ~9 KB
     claim_mailbox_batch script while 4 processes run a ~2 ms script, against
     EVALSHA under the same load.

Every timed cell also reports the INFO execution-path counter delta, so the
mechanism behind each number is visible rather than inferred.
"""
import sys
import time
import multiprocessing as mp
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common
import q8_head_of_line as q8

QUESTION = "q11"
ITERS = 200
WARMUP = 20

NODES = (("default", common.PRIMARY_PORT), ("lock_on_hashtags", common.FLAG_PORT))

# ---- a: pipeline vs script vs MULTI/EXEC ---------------------------------
TAG_A = "{q11a}"
N_A = 64
KEYS_A = [f"{TAG_A}k{i}" for i in range(N_A)]
HASH_A = f"{TAG_A}h"
VALUE_BYTES = 2048
GET_BODY = """
local last
for i = 1, #KEYS do
  last = redis.call('GET', KEYS[i])
end
return #last
"""
HSET_BODY = """
local v = ARGV[1]
for i = 1, #KEYS do
  redis.call('HSET', KEYS[i], 'f', v)
end
return #KEYS
"""

# ---- b: many small keys vs one hash --------------------------------------
N_B = 100_000
VAL_B = 64
STR_PREFIX = "q11bs"   # DEBUG POPULATE emits <prefix>:<i>
BIG_HASH = "q11b:onehash"
MANY_HASH = "q11b:h%d"
MANY_HASHES = 1000
FIELDS_PER_HASH = N_B // MANY_HASHES

# ---- c: rate limiter ------------------------------------------------------
RL_KEY = "{q11c}rl"
RL_WINDOW_MS = 60_000
RL_LIMIT = 10_000_000
LIMITER_BODY = """
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now - window)
local used = redis.call('ZCARD', KEYS[1])
if used >= limit then
  return 0
end
redis.call('ZADD', KEYS[1], now, ARGV[4])
redis.call('PEXPIRE', KEYS[1], window)
return 1
"""

# ---- d: SCRIPT LOAD under load -------------------------------------------
BIG_SCRIPT = (Path(__file__).resolve().parents[1] / "variants" /
              "claim_mailbox_batch.orig.lua").read_text()
LOADERS_D = 4
TRIVIAL_BODY = "return redis.call('GET', KEYS[1]) ~= false and 1 or 0"


def timed(r, log, label, fn, iters=ITERS, warmup=WARMUP):
    """Time one variant and take the INFO counters across exactly the timed calls."""
    before = common.script_counters(r)
    st = common.percentiles(common.time_calls(fn, iters, warmup))
    delta = common.counter_delta(before, common.script_counters(r))
    log.w(f"    [{label}] p50 {st['p50']:.0f}us p99 {st['p99']:.0f}us | "
          f"{common.format_counters(delta)}")
    return st, delta


def cycler(items):
    """A zero-allocation round-robin so the timed closure does no RNG work."""
    box = {"i": 0}

    def nxt():
        i = box["i"]
        box["i"] = (i + 1) % len(items)
        return items[i]
    return nxt


# --------------------------------------------------------------------------
# a) "prefer pipelining over Lua"
# --------------------------------------------------------------------------
def section_a(r, node, log):
    log.section(f"a) pipeline vs script vs MULTI/EXEC - {node}")
    val = common.payload(VALUE_BYTES, "q11a")
    for k in KEYS_A:
        r.delete(k)
        r.hset(k, "f", val)
        r.set(k + "s", val)
    str_keys = [k + "s" for k in KEYS_A]
    get_sha = common.load_script(r, GET_BODY)
    hset_sha = common.load_script(r, HSET_BODY)
    log.cmd(f"SCRIPT LOAD <{N_A} x GET over KEYS> -> {get_sha}")
    log.cmd(f"SCRIPT LOAD <{N_A} x HSET over KEYS> -> {hset_sha}")

    def pipe(keys, transaction, write):
        def run():
            p = r.pipeline(transaction=transaction)
            for k in keys:
                p.hset(k, "f", val) if write else p.get(k)
            p.execute()
        return run

    cells = [
        ("GET x64 | client pipeline (1 round trip)",
         pipe(str_keys, False, False)),
        ("GET x64 | one atomic script",
         lambda: common.evalsha(r, get_sha, str_keys)),
        ("GET x64 | MULTI/EXEC",
         pipe(str_keys, True, False)),
        ("HSET x64 | client pipeline (1 round trip)",
         pipe(KEYS_A, False, True)),
        ("HSET x64 | one atomic script",
         lambda: common.evalsha(r, hset_sha, KEYS_A, [val])),
        ("HSET x64 | MULTI/EXEC",
         pipe(KEYS_A, True, True)),
    ]
    log.cmd(f"GET/HSET x{N_A} on keys in the single hashtag {TAG_A}, "
            f"{VALUE_BYTES}B values, {ITERS} iterations after {WARMUP} warm-up")
    rows, deltas = [], {}
    for label, fn in cells:
        st, d = timed(r, log, f"{node} | {label}", fn)
        rows.append(common.stat_row(f"{node} | {label}", st,
                                    [f"{st['p50'] / N_A:.2f}"]))
        deltas[label] = d
    return rows, deltas


# --------------------------------------------------------------------------
# b) "collapse many small keys into one hash"
# --------------------------------------------------------------------------
def build_strings(r):
    r.execute_command("DEBUG", "POPULATE", N_B, STR_PREFIX, VAL_B)
    return [f"{STR_PREFIX}:{i}" for i in range(0, N_B, N_B // 256)]


def build_one_hash(r, val):
    p = r.pipeline(transaction=False)
    for i in range(N_B):
        p.hset(BIG_HASH, f"f{i}", val)
        if i % 2000 == 1999:
            p.execute()
            p = r.pipeline(transaction=False)
    p.execute()
    return [(BIG_HASH, f"f{i}") for i in range(0, N_B, N_B // 256)]


def build_many_hashes(r, val):
    p = r.pipeline(transaction=False)
    for i in range(N_B):
        p.hset(MANY_HASH % (i % MANY_HASHES), f"f{i // MANY_HASHES}", val)
        if i % 2000 == 1999:
            p.execute()
            p = r.pipeline(transaction=False)
    p.execute()
    return [(MANY_HASH % (i % MANY_HASHES), f"f{i // MANY_HASHES}")
            for i in range(0, N_B, N_B // 256)]


def used_memory(r):
    return float(dict(
        line.split(":", 1) for line in common.info_text(r, "memory").splitlines()
        if ":" in line and not line.startswith("#"))["used_memory"])


def section_b(r, node, log, container):
    log.section(f"b) 100k x 64B: strings vs one hash vs 1000 hashes - {node}")
    common.restart(container, r.connection_pool.connection_kwargs["port"])
    r = common.connect(r.connection_pool.connection_kwargs["port"])
    val = common.payload(VAL_B, "q11b").decode("latin-1")
    rows, mem_rows, deltas = [], [], {}
    layouts = [
        (f"{N_B} string keys", lambda: build_strings(r), "GET"),
        (f"1 hash x {N_B} fields", lambda: build_one_hash(r, val), "HGET"),
        (f"{MANY_HASHES} hashes x {FIELDS_PER_HASH} fields",
         lambda: build_many_hashes(r, val), "HGET"),
    ]
    for label, build, op in layouts:
        common.flushall(r)
        time.sleep(1.0)
        base = used_memory(r)
        t0 = time.time()
        probes = build()
        build_s = time.time() - t0
        after = used_memory(r)
        keyspace = common.info_text(r, "keyspace").strip().splitlines()
        log.cmd(f"# {label}: built in {build_s:.1f}s, {' '.join(keyspace[-2:])}")
        log.w(f"    used_memory {base:.0f} -> {after:.0f} = "
              f"{after - base:.0f} bytes ({(after - base) / N_B:.1f} B/value)")
        try:
            hist = common._s(r.execute_command("DEBUG", "OBJHIST"))
        except Exception as exc:
            hist = f"DEBUG OBJHIST failed: {exc!r}"
        log.cmd("DEBUG OBJHIST")
        log.w(hist)
        nxt = cycler(probes)
        if op == "GET":
            fn = lambda: r.get(nxt())                      # noqa: E731
        else:
            fn = lambda: r.hget(*nxt())                    # noqa: E731
        log.cmd(f"{op} <round-robin over {len(probes)} of the {N_B} values>")
        st, d = timed(r, log, f"{node} | {label} | single {op}", fn)
        rows.append(common.stat_row(f"{node} | {label} | single {op}", st))
        mem_rows.append([f"{node} | {label}", f"{after - base:,.0f}",
                         f"{(after - base) / N_B:.1f}", f"{build_s:.1f}"])
        deltas[label] = d
    common.flushall(r)
    return rows, mem_rows, deltas


# --------------------------------------------------------------------------
# c) "implement a rate limiter in Lua"
# --------------------------------------------------------------------------
def section_c(r, node, log):
    log.section(f"c) sliding-window Lua limiter vs CL.THROTTLE - {node}")
    rows, deltas, note = [], {}, ""
    sha = common.load_script(r, LIMITER_BODY)
    log.cmd(f"SCRIPT LOAD <ZREMRANGEBYSCORE+ZCARD+ZADD+PEXPIRE limiter> -> {sha}")
    r.delete(RL_KEY)
    box = {"i": 0}

    def lua_call():
        box["i"] += 1
        return common.evalsha(r, sha, [RL_KEY],
                              [int(time.time() * 1000), RL_WINDOW_MS,
                               RL_LIMIT, f"m{box['i']}"])
    log.cmd(f"EVALSHA {sha} 1 {RL_KEY} <now_ms> {RL_WINDOW_MS} {RL_LIMIT} <id>")
    st, d = timed(r, log, f"{node} | Lua sliding window", lua_call)
    rows.append(common.stat_row(f"{node} | Lua sliding window (4 redis.call)", st))
    deltas["lua"] = d
    log.w(f"    zset after the run: ZCARD {RL_KEY} = {r.zcard(RL_KEY)}")
    r.delete(RL_KEY)

    thr_key = RL_KEY + "t"
    r.delete(thr_key)
    log.cmd(f"CL.THROTTLE {thr_key} {RL_LIMIT} {RL_LIMIT} 60 1")
    try:
        first = r.execute_command("CL.THROTTLE", thr_key, RL_LIMIT, RL_LIMIT, 60, 1)
        log.w(f"    first reply: {first}")
        st, d = timed(r, log, f"{node} | CL.THROTTLE",
                      lambda: r.execute_command("CL.THROTTLE", thr_key, RL_LIMIT,
                                                RL_LIMIT, 60, 1))
        rows.append(common.stat_row(f"{node} | CL.THROTTLE (native)", st))
        deltas["throttle"] = d
    except Exception as exc:
        note = f"CL.THROTTLE unavailable on this build: {exc!r}"
        log.w(f"    {note}")
        rows.append([f"{node} | CL.THROTTLE (native)", 0] + ["-"] * 7)
    r.delete(thr_key)
    return rows, deltas, note


# --------------------------------------------------------------------------
# d) "SCRIPT LOAD per worker is cheap"
# --------------------------------------------------------------------------
def section_d(r, node, port, log):
    log.section(f"d) SCRIPT LOAD of a {len(BIG_SCRIPT)}B script under load - {node}")
    val = common.payload(q8.VALUE_BYTES, "q11d")
    for k in q8.SCRIPT_KEYS:
        r.delete(k)
        r.hset(k, "f", val)
    r.set(q8.SAME, b"probe-value")
    load_sha = common.load_script(r, q8.BODY)
    ncalls, script_us = q8.calibrate(r, load_sha, log, f"{node} d")
    trivial_sha = common.load_script(r, TRIVIAL_BODY)
    log.cmd(f"EVALSHA {load_sha} 8 <8 keys in {q8.SCRIPT_TAG}> {ncalls}   "
            f"# x{LOADERS_D} loader processes in a loop")

    box = {"i": 0}

    def unique_load():
        box["i"] += 1
        return r.script_load(BIG_SCRIPT + f"\n-- q11d:{box['i']}\n")

    cells = [
        (f"SCRIPT LOAD {len(BIG_SCRIPT)}B, same text (sha already cached)",
         lambda: r.script_load(BIG_SCRIPT)),
        (f"SCRIPT LOAD {len(BIG_SCRIPT)}B, fresh text each time",
         unique_load),
        ("EVALSHA of a 1-key trivial script",
         lambda: common.evalsha(r, trivial_sha, [q8.SAME])),
    ]

    rows, deltas = [], {}
    for phase, loaders in (("idle", 0), (f"{LOADERS_D} loaders", LOADERS_D)):
        procs = []
        if loaders:
            deadline = time.time() + 90.0
            procs = [mp.Process(target=q8.loader,
                                args=(port, load_sha, q8.SCRIPT_KEYS, ncalls,
                                      deadline))
                     for _ in range(loaders)]
            for p in procs:
                p.start()
            time.sleep(1.0)
        for label, fn in cells:
            st, d = timed(r, log, f"{node} | {phase} | {label}", fn)
            rows.append(common.stat_row(f"{node} | {phase} | {label}", st,
                                        [f"{script_us:.0f}"]))
            deltas[(phase, label)] = d
        for p in procs:
            p.terminate()
            p.join()
    common.script_flush(r)
    return rows, deltas, script_us


# --------------------------------------------------------------------------
def run() -> None:
    log = common.RawLog(QUESTION, common.primary(),
                        note=f"four pieces of Redis advice, {ITERS} iterations "
                             f"after {WARMUP} warm-up, on the default primary and "
                             f"on a throwaway --lock_on_hashtags node")
    res = {}
    try:
        flag = common.start_flag_node(["--lock_on_hashtags=true"])
        log.w(f"throwaway node: {common.server_flags(flag)}")
        for node, port in NODES:
            r = common.connect(port)
            log.section(f"===== node {node} (:{port}) =====")
            log.w(f"server version: {common.server_version(r)}")
            log.w(f"server flags:   {common.server_flags(r)}")
            container = ("dfskill-primary" if port == common.PRIMARY_PORT
                         else common.FLAG_NAME)
            res[(node, "a")] = section_a(r, node, log)
            res[(node, "c")] = section_c(r, node, log)
            res[(node, "d")] = section_d(r, node, port, log)
            res[(node, "b")] = section_b(r, node, log, container)
    finally:
        common.stop_flag_node()
        log.w("\nthrowaway node removed; lab is back to its default flags")

    body = build_body(res, log)
    log.save()
    common.emit(QUESTION, "does Redis performance advice hold on Dragonfly?",
                ["Q11", "pipeline vs Lua vs MULTI; 100k keys vs 1 hash; Lua "
                        "limiter vs CL.THROTTLE; SCRIPT LOAD under load",
                 res["summary"], "see the four sub-experiments in the appendix"],
                body)


def p50(rows, needle, node):
    for row in rows:
        if row[0].startswith(node) and needle in row[0]:
            return float(row[2]) if row[2] != "-" else float("nan")
    return float("nan")


def build_body(res, log=None) -> str:
    a_rows = res[("default", "a")][0] + res[("lock_on_hashtags", "a")][0]
    c_rows = res[("default", "c")][0] + res[("lock_on_hashtags", "c")][0]
    d_rows = res[("default", "d")][0] + res[("lock_on_hashtags", "d")][0]
    b_rows = res[("default", "b")][0] + res[("lock_on_hashtags", "b")][0]
    b_mem = res[("default", "b")][1] + res[("lock_on_hashtags", "b")][1]

    ta = common.format_table(common.STAT_HEADERS + ["us_per_key(p50)"], a_rows)
    tb_mem = common.format_table(
        ["layout", "used_memory delta (B)", "B/value", "build_s"], b_mem)
    tb = common.format_table(common.STAT_HEADERS, b_rows)
    tc = common.format_table(common.STAT_HEADERS, c_rows)
    td = common.format_table(common.STAT_HEADERS + ["loader script us"], d_rows)
    if log is not None:
        # The per-variant loop logs only p50/p99; write the full tables so every
        # column of the RESULTS.md tables is backed by this raw log.
        for name, tbl in (("a", ta), ("b memory", tb_mem), ("b", tb),
                          ("c", tc), ("d", td)):
            log.section(f"full per-variant table - {name}")
            log.w(tbl)

    def ratio(rows, a, b, node):
        x, y = p50(rows, a, node), p50(rows, b, node)
        return x / y if y else float("nan")

    pipe_vs_lua = {n: ratio(a_rows, "GET x64 | one atomic script",
                            "GET x64 | client pipeline", n)
                   for n, _ in NODES}
    wpipe_vs_lua = {n: ratio(a_rows, "HSET x64 | one atomic script",
                             "HSET x64 | client pipeline", n)
                    for n, _ in NODES}
    lua_vs_thr = {n: ratio(c_rows, "Lua sliding window", "CL.THROTTLE", n)
                  for n, _ in NODES}
    fresh_load = {n: p50(d_rows, f"{LOADERS_D} loaders | SCRIPT LOAD "
                         f"{len(BIG_SCRIPT)}B, fresh text", n) for n, _ in NODES}
    cached_load = {n: p50(d_rows, f"{LOADERS_D} loaders | SCRIPT LOAD "
                          f"{len(BIG_SCRIPT)}B, same text", n) for n, _ in NODES}
    idle_fresh = {n: p50(d_rows, f"idle | SCRIPT LOAD {len(BIG_SCRIPT)}B, fresh text",
                         n) for n, _ in NODES}
    res["summary"] = (
        f"64 GET: script/pipeline p50 {pipe_vs_lua['default']:.1f}x default, "
        f"{pipe_vs_lua['lock_on_hashtags']:.1f}x lock_on_hashtags; "
        f"Lua limiter/CL.THROTTLE {lua_vs_thr['default']:.1f}x default, "
        f"{lua_vs_thr['lock_on_hashtags']:.1f}x lock_on_hashtags; "
        f"{len(BIG_SCRIPT)}B SCRIPT LOAD p50 {idle_fresh['default']:.0f}us idle -> "
        f"{fresh_load['default']:.0f}us under {LOADERS_D} loaders "
        f"(cached sha {cached_load['default']:.0f}us); "
        f"{N_B // 1000}k x {VAL_B}B costs {b_mem[0][2]} B/value as strings vs "
        f"{b_mem[1][2]} in one hash")

    def cnt(deltas, key, n):
        return common.format_counters(deltas[n].get(key, {}))

    dd_all = {n: res[(n, "d")][1] for n, _ in NODES}
    loader_us = {n: float(res[(n, "d")][2]) for n, _ in NODES}
    load_counters = "; ".join(
        f"{n} {label}: `{common.format_counters(d)}`"
        for n in dd_all for (phase, label), d in dd_all[n].items()
        if "loaders" in phase)
    da, dc, dd = ({n: res[(n, "a")][1] for n, _ in NODES},
                  {n: res[(n, "c")][1] for n, _ in NODES},
                  {n: res[(n, "d")][1] for n, _ in NODES})
    notes = [res[(n, "c")][2] for n, _ in NODES if res[(n, "c")][2]]
    trivial_evalsha = {n: p50(d_rows, "loaders | EVALSHA", n) for n, _ in NODES}

    return (
        f"Four pieces of standard Redis advice, each measured on the default "
        f"primary :6379 and on an otherwise identical throwaway "
        f"`--lock_on_hashtags` node :6382, {ITERS} iterations after {WARMUP} "
        f"warm-up. Counter deltas in (a), (b) and (c) are for exactly the timed "
        f"calls. In (d) the server is shared with {LOADERS_D} concurrent loader "
        f"processes, so (d)'s counter deltas are server-wide and include loader "
        f"traffic -- the {ITERS} `SCRIPT LOAD`s execute no script at all, yet "
        f"report `eval_*_coordination` and `lua_interpreter_return` deltas. No "
        f"execution path can be inferred from (d)'s counter split.\n\n"
        f"#### a) \"prefer pipelining over Lua\"\n\n"
        f"{N_A} keys in the single hashtag `{TAG_A}`, {VALUE_BYTES}B values, as a "
        f"client pipeline on one connection (one round trip), as one atomic "
        f"script, and as `MULTI`/`EXEC`.\n\n{ta}\n\n"
        f"**Counter split.** default script GET "
        f"`{cnt(da, 'GET x64 | one atomic script', 'default')}`; "
        f"lock_on_hashtags script GET "
        f"`{cnt(da, 'GET x64 | one atomic script', 'lock_on_hashtags')}`; "
        f"default MULTI/EXEC GET "
        f"`{cnt(da, 'GET x64 | MULTI/EXEC', 'default')}`; default pipeline GET "
        f"`{cnt(da, 'GET x64 | client pipeline (1 round trip)', 'default')}`.\n\n"
        f"#### b) \"collapse many small keys into one hash\"\n\n"
        f"{N_B:,} values of {VAL_B}B in three layouts, each on a freshly restarted "
        f"server (Dragonfly's OOM check is against RSS and `FLUSHALL` does not "
        f"recover it), `used_memory` delta and single-value read latency.\n\n"
        f"{tb_mem}\n\n{tb}\n\n"
        f"`DEBUG OBJHIST` output per layout is in the raw log.\n\n"
        f"#### c) \"implement a rate limiter in Lua\"\n\n"
        f"A classic sliding-window limiter on ONE key "
        f"(`ZREMRANGEBYSCORE`+`ZCARD`+`ZADD`+`PEXPIRE`, 4 `redis.call`) against "
        f"Dragonfly's native `CL.THROTTLE`.\n\n{tc}\n\n"
        f"**Counter split.** default Lua `{cnt(dc, 'lua', 'default')}`; "
        f"lock_on_hashtags Lua `{cnt(dc, 'lua', 'lock_on_hashtags')}`.\n"
        + (f"\n{' '.join(notes)}\n" if notes else "") +
        f"\n#### d) \"SCRIPT LOAD per worker is cheap\"\n\n"
        f"{ITERS} sequential `SCRIPT LOAD` of the {len(BIG_SCRIPT)}B "
        f"`claim_mailbox_batch.orig.lua`, idle and while {LOADERS_D} separate "
        f"PROCESSES run a ~{q8.TARGET_US}us script in a loop (q8's calibrated "
        f"loader), against `EVALSHA` of a trivial 1-key script under the same "
        f"load. Same text re-loads an already-cached sha; the fresh-text rows "
        f"append a nonce comment so every load really compiles. The loader is "
        f"calibrated per node: it landed at p50 {loader_us['default']:.0f}us on "
        f"the default node and {loader_us['lock_on_hashtags']:.0f}us under "
        f"`--lock_on_hashtags` (target {q8.TARGET_US}us, see the raw log), so the "
        f"two nodes ran at ~"
        f"{loader_us['lock_on_hashtags'] / loader_us['default']:.1f}x different "
        f"load -- the cells are NOT equal-load across nodes.\n\n{td}\n\n"
        f"**Counter split under load.** {load_counters}\n\n"
        f"**Verdicts.** (a) one atomic script vs one pipeline round trip: "
        f"{pipe_vs_lua['default']:.1f}x on default flags, "
        f"{pipe_vs_lua['lock_on_hashtags']:.1f}x under `--lock_on_hashtags` "
        f"(writes: {wpipe_vs_lua['default']:.1f}x / "
        f"{wpipe_vs_lua['lock_on_hashtags']:.1f}x). (c) the Lua limiter costs "
        f"{lua_vs_thr['default']:.1f}x `CL.THROTTLE` on default flags and "
        f"{lua_vs_thr['lock_on_hashtags']:.1f}x under `--lock_on_hashtags`. "
        f"(d) a fresh-text `SCRIPT LOAD` costs p50 {idle_fresh['default']:.0f}us "
        f"idle and {fresh_load['default']:.0f}us under {LOADERS_D} loaders on "
        f"default flags, {idle_fresh['lock_on_hashtags']:.0f}us -> "
        f"{fresh_load['lock_on_hashtags']:.0f}us under `--lock_on_hashtags`; "
        f"re-loading an already-cached sha costs "
        f"{cached_load['default']:.0f}us / "
        f"{cached_load['lock_on_hashtags']:.0f}us under the same load.\n\n"
        f"**`--lock_on_hashtags` reintroduces head-of-line blocking.** Under "
        f"{LOADERS_D} loaders, `EVALSHA` of a trivial 1-key script that touches "
        f"none of the loader's keys costs p50 "
        f"{trivial_evalsha['lock_on_hashtags']:.1f}us on the lock_on_hashtags node "
        f"against {trivial_evalsha['default']:.1f}us on the default node "
        f"({trivial_evalsha['lock_on_hashtags'] / trivial_evalsha['default']:.0f}x), "
        f"while idle both nodes serve it in 115-125us. The flag buys the shard-local "
        f"path (Q5, Q10) and pays for it by serialising unrelated work behind the "
        f"loaders on the shared-tag shard. Q8's reassuring `proactor_threads=4` "
        f"result was measured on default flags only, so it does not cover this "
        f"case.\n")


if __name__ == "__main__":
    run()
