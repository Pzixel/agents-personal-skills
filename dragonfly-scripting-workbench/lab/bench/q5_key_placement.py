#!/usr/bin/env python
"""Q5 - key placement: the same 256 `redis.call`s over 1 key, over 8 keys in ONE
hashtag, and over 8 keys in EIGHT hashtags, at --proactor_threads=4.

This is the decisive experiment for part A's cross-cutting observation that
`redis.call` costs ~0.6us in a 1-key script but ~29us in a 4-6 key script: call
count and payload size are held constant, ONLY key placement varies.

Each shape is run atomic (default) and with `--!df flags=disable-atomicity`
(the real Dragonfly directive; `#!lua flags=...` is a Lua syntax error here),
plus one `allow-undeclared-keys` run with an EMPTY KEYS array.

Extra server flags that need a restart (`--lock_on_hashtags`,
`--lua_auto_async=true`) are measured on a throwaway container `dfskill-flag`
on port 6382 so the lab keeps its default flags.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common

QUESTION = "q5"
NKEYS = 8
NCALLS = 256            # redis.call count, identical in every variant
VALUE_BYTES = 2048
ITERS = 200
WARMUP = 20
FLAG_PORT = common.FLAG_PORT      # throwaway node, see common.start_flag_node
FLAG_NAME = common.FLAG_NAME
BASE_ARGS = common.BASE_ARGS
IMAGE = common.IMAGE

ONE_KEY = ["{q5one}k0"]
ONE_TAG = [f"{{q5tag}}k{i}" for i in range(NKEYS)]
MANY_TAG = [f"{{q5t{i}}}k{i}" for i in range(NKEYS)]

READ_BODY = """
local n = tonumber(ARGV[1])
local last
for i = 1, n do
  last = redis.call('HGET', KEYS[(i - 1) %% #KEYS + 1], 'f')
end
return #last
"""

READ_UNDECLARED = """--!df flags=allow-undeclared-keys
local n = tonumber(ARGV[1])
local last
for i = 1, n do
  last = redis.call('HGET', ARGV[(i - 1) %% (#ARGV - 1) + 2], 'f')
end
return #last
"""

WRITE_BODY = """
local n = tonumber(ARGV[1])
local v = ARGV[2]
for i = 1, n do
  redis.call('HSET', KEYS[(i - 1) %% #KEYS + 1], 'w' .. i, v)
end
return n
"""

DISABLE = "--!df flags=disable-atomicity\n"

Q5_HEADERS = common.STAT_HEADERS + ["shape", "us_per_call(p50)",
                                    "shardlocal/io per call"]

# Hop microbenchmark: 256 DISTINCT keys, one GET each, all in one hashtag vs
# spread over 4 hashtags (= the 4 proactor shards).
HOPN = 256
HOP_ONE = [f"{{q5hop}}g{i}" for i in range(HOPN)]
HOP_FOUR = [f"{{q5h{i % 4}}}g{i}" for i in range(HOPN)]
HOP_BODY = """
local last
for i = 1, #KEYS do
  last = redis.call('GET', KEYS[i])
end
return #last
"""


def hop_micro(r, log, node_label):
    """N GETs on N distinct keys: 1 hashtag vs 4 hashtags. Cleanest isolation of
    the per-`redis.call` cost, since each call touches its own key."""
    val = common.payload(64, "hop")
    rows = []
    for name, keys in ((f"{HOPN} GET / 1 hashtag", HOP_ONE),
                       (f"{HOPN} GET / 4 hashtags", HOP_FOUR)):
        pipe = r.pipeline()
        for k in keys:
            pipe.set(k, val)
        pipe.execute()
        _, row, _ = measure(r, log, name, HOP_BODY, keys, [],
                            f"{HOPN}k/{1 if keys is HOP_ONE else 4}tag", HOPN)
        rows.append(row)
    log.section(f"[{node_label}] hop microbenchmark ({HOPN} GET on {HOPN} distinct keys)")
    t = common.format_table(Q5_HEADERS, rows)
    log.w(t)
    return t, rows


def seed(r, keys, val):
    for k in keys:
        r.delete(k)
        r.hset(k, "f", val)


def measure(r, log, label, body, keys, argv, shape_keys, ncalls=NCALLS):
    """One variant: load, sanity-call, time ITERS+WARMUP invocations and take the
    INFO execution-path counters across exactly those invocations."""
    sha = common.load_script(r, body)
    log.cmd(f"SCRIPT LOAD <{label}> -> {sha}")
    log.cmd(f"EVALSHA {sha} {len(keys)} {' '.join(keys)[:180] if keys else '(no keys)'} "
            f"{' '.join(str(a)[:24] for a in argv)[:120]}")
    common.evalsha(r, sha, keys, argv)
    before = common.script_counters(r)
    st = common.percentiles(common.time_calls(
        lambda: common.evalsha(r, sha, keys, argv), ITERS, WARMUP))
    delta = common.counter_delta(before, common.script_counters(r))
    n = ITERS + WARMUP
    path = (f"{delta.get('eval_shardlocal_coordination_total', 0) / n:.2f}/"
            f"{delta.get('eval_io_coordination_total', 0) / n:.2f}")
    log.w(f"    [{label}] counters over {n} invocations: "
          f"{common.format_counters(delta)}")
    row = common.stat_row(label, st, [shape_keys, round(st["p50"] / ncalls, 3), path])
    return sha, row, delta


def run_matrix(r, log, node_label):
    """The full shape x atomicity matrix on one server. Returns (rows, shas)."""
    val = common.payload(VALUE_BYTES, "q5v")
    for keys in (ONE_KEY, ONE_TAG, MANY_TAG):
        seed(r, keys, val)
    rows, shas = [], {}
    shapes = [("1 key (shard-local)", ONE_KEY, 1),
              (f"{NKEYS} keys, 1 hashtag", ONE_TAG, 1),
              (f"{NKEYS} keys, {NKEYS} hashtags", MANY_TAG, NKEYS)]
    for name, keys, ntags in shapes:
        for atom, prefix in (("atomic", ""), ("disable-atomicity", DISABLE)):
            label = f"{NCALLS} HGET / {name} / {atom}"
            sha, row, _ = measure(r, log, label, prefix + READ_BODY % (),
                                  keys, [NCALLS], f"{len(keys)}k/{ntags}tag")
            rows.append(row)
            shas[label] = sha
    # allow-undeclared-keys: EMPTY KEYS, key names passed as ARGV. Tested on BOTH
    # shapes because the prediction is that it disables the single-shard fast path,
    # i.e. it should make the ONE-hashtag script slower, not faster.
    for name, keys, ntags in (shapes[1], shapes[2]):
        label = f"{NCALLS} HGET / {name} / allow-undeclared-keys"
        sha, row, _ = measure(r, log, label, READ_UNDECLARED % (), [],
                              [NCALLS] + keys, f"0k decl/{ntags}tag")
        rows.append(row)
        shas[label] = sha
    # writes with discarded return values -- what --lua_auto_async targets
    for name, keys, ntags in (shapes[1], shapes[2]):
        label = f"{NCALLS} HSET (discarded) / {name}"
        sha, row, _ = measure(r, log, label, WRITE_BODY % (), keys,
                              [NCALLS, common.payload(64, "w")], f"{len(keys)}k/{ntags}tag")
        rows.append(row)
        shas[label] = sha
    log.section(f"[{node_label}] SCRIPT LATENCY (usec; includes warm-up)")
    lat = common.script_latency_text(r)
    log.cmd("SCRIPT LATENCY")
    for label, sha in shas.items():
        e = common.parse_script_latency(lat, sha)
        log.w(f"[{label}] {sha}: {common.format_script_latency(e)}")
    return rows


def run() -> None:
    r = common.primary()
    log = common.RawLog(QUESTION, r,
                        note=f"{NCALLS} HGET per script, {VALUE_BYTES}B values, "
                             f"identical call count in every variant; only key "
                             f"placement / atomicity / server flags vary")
    tables = []
    rows = run_matrix(r, log, "default")
    t = common.format_table(Q5_HEADERS, rows)
    log.section("default flags (dfskill-primary :6379)")
    log.w(t)
    tables.append(("default flags (`--proactor_threads=4`, primary :6379)", t, rows))
    hop_t, hop_rows = hop_micro(r, log, "default")
    tables.append((f"hop microbenchmark: {HOPN} `GET` on {HOPN} distinct keys "
                   f"(default flags)", hop_t, hop_rows))

    for extra, title in (
        (["--lock_on_hashtags"], "--lock_on_hashtags"),
        (["--lua_auto_async=true"], "--lua_auto_async=true"),
    ):
        log.section(f"restart with {title} -> {FLAG_NAME} :{FLAG_PORT}")
        log.cmd(f"docker run -d --name {FLAG_NAME} -p {FLAG_PORT}:6379 {IMAGE} "
                f"{' '.join(BASE_ARGS + extra)}")
        rf = common.start_flag_node(extra)
        log.w(f"server version: {common.server_version(rf)}")
        log.w(f"server flags:   {' '.join(BASE_ARGS + extra)}")
        rr = run_matrix(rf, log, title)
        tf = common.format_table(Q5_HEADERS, rr)
        log.w(tf)
        tables.append((f"`{title}` (throwaway node :{FLAG_PORT})", tf, rr))
    common.stop_flag_node()
    log.cmd(f"docker rm -f {FLAG_NAME}   # lab back to default flags")

    base = tables[0][2]
    lock_rows = tables[2][2]
    auto_rows = tables[3][2]
    lock_one_tag = lock_rows[2][2] / NCALLS
    lock_many_tag = lock_rows[4][2] / NCALLS
    auto_write_one = auto_rows[8][2] / NCALLS
    base_write_one = base[8][2] / NCALLS
    hop_one_us = hop_rows[0][2] / HOPN
    hop_four_us = hop_rows[1][2] / HOPN
    one_key = base[0][2] / NCALLS
    one_key_noatom = base[1][2] / NCALLS
    lock_one_key = lock_rows[0][2] / NCALLS
    lock_one_key_noatom = lock_rows[1][2] / NCALLS
    auto_one_key = auto_rows[0][2] / NCALLS
    auto_one_key_noatom = auto_rows[1][2] / NCALLS
    one_tag = base[2][2] / NCALLS
    many_tag = base[4][2] / NCALLS
    many_noatom = base[5][2] / NCALLS
    body = [
        f"{ITERS} measured iterations after {WARMUP} warm-up; every variant issues "
        f"exactly **{NCALLS} `redis.call`** with {VALUE_BYTES}B values, so "
        f"`us_per_call` is directly comparable. `us_per_call(p50)` = client p50 / "
        f"{NCALLS}.\n",
    ]
    for title, tbl, _ in tables:
        body.append(f"**{title}**\n\n{tbl}\n")
    body.append(
        f"**Verdict on the 1-key vs 8-key `redis.call`.** With call count and payload "
        f"held constant, one `redis.call` costs **{one_key:.2f}us** against 1 key, "
        f"**{one_tag:.2f}us** against 8 keys in ONE hashtag and **{many_tag:.2f}us** "
        f"against 8 keys in EIGHT hashtags. `--!df flags=disable-atomicity` on the "
        f"8-hashtag shape: {many_noatom:.2f}us/call.\n\n"
        f"Hop microbenchmark ({HOPN} `GET` on {HOPN} distinct keys): "
        f"**{hop_one_us:.2f}us/call** in one hashtag vs **{hop_four_us:.2f}us/call** "
        f"over 4 hashtags. The `shardlocal/io per call` column is the INFO "
        f"`eval_shardlocal_coordination_total` / `eval_io_coordination_total` delta "
        f"divided by the number of script invocations, i.e. which execution path "
        f"each variant actually took.\n\n"
        f"**Mechanism.** The counters say the split is shard-local vs io-coordinated, "
        f"and a shared hashtag does NOT by itself buy the shard-local path: on "
        f"default flags the 8-keys-in-one-hashtag script reports "
        f"`eval_io_coordination_total +1` per invocation, exactly like the 8-hashtag "
        f"script, and costs the same per call. The hop microbenchmark agrees "
        f"({hop_one_us:.2f} vs {hop_four_us:.2f}us/call for 1 vs 4 hashtags). Only "
        f"**`--lock_on_hashtags`** co-locates a hashtag on one shard: with it the "
        f"one-hashtag script flips to `eval_shardlocal_coordination_total +1` and "
        f"**{lock_one_tag:.2f}us/call** ({one_tag:.2f}us/call without it), while the "
        f"8-hashtag script stays io-coordinated at {lock_many_tag:.2f}us/call. The "
        f"1-key script is shard-local **only when it runs atomically**, and its "
        f"cost differs per node: {one_key:.2f}us/call on the default primary, "
        f"{lock_one_key:.2f}us/call under `--lock_on_hashtags` and "
        f"{auto_one_key:.2f}us/call under `--lua_auto_async` (both throwaway nodes "
        f"run as a co-tenant of the default primary). With "
        f"`--!df flags=disable-atomicity` the 1-key script reports `0.00/1.00` -- "
        f"io-coordinated -- on all three nodes ({one_key_noatom:.2f} / "
        f"{lock_one_key_noatom:.2f} / {auto_one_key_noatom:.2f}us/call), i.e. the "
        f"directive takes even the 1-key script off the shard-local path.\n\n"
        f"**Fast-path killers.** `--!df flags=disable-atomicity` and "
        f"`--!df flags=allow-undeclared-keys` both move the script off "
        f"`eval_shardlocal_coordination_total` onto `eval_io_coordination_total` "
        f"even for a single-shard script (see the counter column). Under "
        f"`--lock_on_hashtags`, where there is a fast path to lose, that costs "
        f"real time on the one-hashtag shape; on default flags the script was "
        f"already io-coordinated, so the directives change the counter without "
        f"changing the latency.\n\n"
        f"**`--lua_auto_async=true`** only affects `redis.call` whose result is "
        f"discarded: the 256-`HSET`-discarded script goes {base_write_one:.2f} -> "
        f"{auto_write_one:.2f}us/call, while every read variant is unchanged.\n")
    log.save()
    common.emit(QUESTION, "key placement (1 key vs 8 keys/1 tag vs 8 keys/8 tags)",
                ["Q5", f"{NCALLS} HGET, 1 key vs 8 keys/1 tag vs 8 keys/8 tags "
                       f"(+disable-atomicity, lock_on_hashtags, lua_auto_async)",
                 f"us/call {one_key:.2f} (1 key) / {one_tag:.2f} (1 tag) / "
                 f"{many_tag:.2f} (8 tags); hop {hop_one_us:.2f} vs {hop_four_us:.2f}",
                 "see verdict in the appendix"],
                "\n".join(body))


if __name__ == "__main__":
    run()
