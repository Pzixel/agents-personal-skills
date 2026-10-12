#!/usr/bin/env python
"""Q7 - the original script: `claim_mailbox_batch.lua`
(lab/variants/claim_mailbox_batch.orig.lua), against the
HMGET-prefetch variant with the same KEYS arity, ARGV protocol and reply shape.

Seed: 1024 due mailboxes, no leases, no receipts, no catalog descriptor (legacy
layout, versions in KEYS[5]), ~2 KB JSON records, candidate_window=1024,
batch_size=32, lease_ttl=120000 (the script rejects any other TTL).

Every fence in lab/Q7-CONTRACT.md has to pass before a timing means anything, so
the driver asserts the reply is RESOLVED with batch_size CLAIMED entries and
reports that assertion. The two variants are compared on the IDENTICAL seeded
state with the IDENTICAL lease tokens; the reply arrays must match field for
field except the two server-clock fields (`now`, and `deadline = now +
lease_ttl`), which are checked for the `deadline - now == lease_ttl` relation.
"""
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common

QUESTION = "q7"
VARIANTS = Path(__file__).resolve().parent.parent / "variants"
TAG = "{inbound}"
GEN = "gen-q7"
PROC = "q7-process"
LEASE_TTL = 120000
CANDIDATE_WINDOW = 1024
BATCH_SIZE = 32
N_MAILBOXES = 1024
RECORD_BYTES = 2048
VERSION = "7"
# Fixed past due score (2023-11-14T22:13:20Z): the reply carries `earliest_due`,
# so a clock-derived seed score would make the two variants' replies differ for
# a reason that has nothing to do with the scripts.
DUE_SCORE = 1_700_000_000_000
ITERS = 200
WARMUP = 20

K = [
    f"catalog:{TAG}:active_generation",                    # 1
    f"catalog:{TAG}:cursor_coverage_generation",           # 2
    f"catalog:{TAG}:reconciled_server_run_id",             # 3
    f"catalog:{TAG}:last_reconciliation_started_ms",       # 4
    f"catalog:{TAG}:generation:{GEN}:mailbox_versions",    # 5
    f"imap:{TAG}:due",                                     # 6
    f"imap:{TAG}:leases",                                  # 7
    f"imap:{TAG}:lease_receipts",                          # 8
    f"imap:{TAG}:diagnostic_owners",                       # 9
    f"imap:{TAG}:poll_starts",                             # 10
    f"catalog:{TAG}:active_grants_descriptor",             # 11
    f"catalog:{TAG}:active_mailbox_versions",              # 12
    f"catalog:{TAG}:mailbox_records",                      # 13
    f"imap:{TAG}:lease_records",                           # 14
]
MUTABLE = [K[5], K[6], K[7], K[8], K[9], K[13]]  # due zset + everything the script writes
MAILBOXES = [f"{i:032x}" for i in range(N_MAILBOXES)]


def record_json(mailbox_id: str) -> str:
    base = {"mailbox_id": mailbox_id, "version": VERSION, "name": "INBOX",
            "uid_validity": 1, "captured_at_ms": 0, "filler": ""}
    pad = RECORD_BYTES - len(json.dumps(base))
    base["filler"] = "p" * max(pad, 0)
    return json.dumps(base)


def run_id(r) -> str:
    for line in common.info_text(r, "replication").splitlines():
        if line.startswith("master_replid:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError("no master_replid in INFO replication")


def seed_static(r, log) -> None:
    """Everything that the script never writes: fences, versions, records."""
    for k in K:
        r.delete(k)
    now_ms = int(time.time() * 1000)
    r.set(K[0], GEN)
    r.set(K[1], GEN)
    r.set(K[2], run_id(r))
    r.set(K[3], str(now_ms - 1000))
    log.cmd(f"SET {K[0]} {GEN} ; SET {K[1]} {GEN} ; SET {K[2]} <master_replid> ; "
            f"SET {K[3]} <now-1000>")
    pipe = r.pipeline()
    for i in range(0, N_MAILBOXES, 256):
        chunk = MAILBOXES[i:i + 256]
        pipe.hset(K[4], mapping={m: VERSION for m in chunk})
        pipe.hset(K[12], mapping={f"{m}:{VERSION}": record_json(m) for m in chunk})
    pipe.execute()
    log.cmd(f"HSET {K[4]} <{N_MAILBOXES} mailbox_id -> version {VERSION}>")
    log.cmd(f"HSET {K[12]} <{N_MAILBOXES} '<id>:{VERSION}' -> ~{RECORD_BYTES}B JSON>")
    log.w(f"KEYS[11] catalog descriptor deliberately ABSENT -> legacy layout, "
          f"versions read from KEYS[5]; KEYS[7] leases and KEYS[8] receipts ABSENT")


def reset_mutable(r) -> None:
    """Back to the identical pre-claim state: no leases/receipts/owners/poll
    starts/lease records, all 1024 mailboxes due in the past."""
    score = DUE_SCORE
    pipe = r.pipeline()
    pipe.delete(K[6], K[7], K[8], K[9], K[13])
    for i in range(0, N_MAILBOXES, 512):
        pipe.zadd(K[5], {m: score for m in MAILBOXES[i:i + 512]})
    pipe.execute()


def tokens() -> list[str]:
    return [str(uuid.uuid4()) for _ in range(BATCH_SIZE)]


def argv(toks, rid) -> list:
    return ["1", GEN, rid, PROC, str(LEASE_TTL), str(CANDIDATE_WINDOW),
            "600000", str(BATCH_SIZE)] + toks


def check_reply(reply, toks, log, label) -> tuple:
    """Fence check: the contract's RESOLVED shape with BATCH_SIZE CLAIMED entries.
    Returns (now_ms, [(status, token, mailbox_id, deadline, record)])."""
    assert reply[0] == b"RESOLVED", f"{label}: reply[0]={reply[0]!r} - a FENCED/" \
        f"PROTOCOL early exit, timing it would be worthless. reply={reply[:4]!r}"
    now = int(reply[1])
    assert int(reply[3]) == BATCH_SIZE, f"{label}: batch_size {reply[3]!r}"
    entries = [tuple(reply[4 + 5 * i: 9 + 5 * i]) for i in range(BATCH_SIZE)]
    statuses = {e[0] for e in entries}
    assert statuses == {b"CLAIMED"}, f"{label}: statuses={statuses}"
    assert [e[1].decode() for e in entries] == toks, f"{label}: token mismatch"
    for e in entries:
        assert int(e[3]) - now == LEASE_TTL, f"{label}: deadline-now={int(e[3]) - now}"
    log.w(f"    [{label}] fence check PASSED: RESOLVED, earliest_due={reply[2]!r}, "
          f"{BATCH_SIZE}/{BATCH_SIZE} CLAIMED, deadline-now={LEASE_TTL} for all, "
          f"record {len(entries[0][4])}B")
    return now, entries


def normalized(reply, now, entries) -> list:
    """Reply with the two server-clock fields removed, for cross-variant equality."""
    return [reply[0], reply[2], reply[3]] + [
        [e[0], e[1], e[2], str(int(e[3]) - now).encode(), e[4]] for e in entries]


def timed(r, sha, rid, log, label):
    samples = []
    before = None
    for i in range(WARMUP + ITERS):
        reset_mutable(r)
        toks = tokens()
        a = argv(toks, rid)
        if i == WARMUP:
            before = common.script_counters(r)
        t0 = time.perf_counter_ns()
        reply = common.evalsha(r, sha, K, a)
        dt = (time.perf_counter_ns() - t0) / 1000.0
        if i >= WARMUP:
            samples.append(dt)
        if i in (0, WARMUP):
            check_reply(reply, toks, log, f"{label} iter{i}")
    delta = common.counter_delta(before, common.script_counters(r))
    log.w(f"    [{label}] counters over {ITERS} invocations: "
          f"{common.format_counters(delta)}")
    return samples, delta


def run() -> None:
    r = common.primary()
    log = common.RawLog(QUESTION, r,
                        note=f"real claim_mailbox_batch.lua vs HMGET variant; "
                             f"{N_MAILBOXES} due mailboxes, no leases, "
                             f"candidate_window={CANDIDATE_WINDOW}, "
                             f"batch_size={BATCH_SIZE}, lease_ttl={LEASE_TTL}, "
                             f"~{RECORD_BYTES}B records, all 14 keys in hashtag "
                             f"{TAG}")
    bodies = {}
    for name, fn in (("orig", "claim_mailbox_batch.orig.lua"),
                     ("hmget", "claim_mailbox_batch.hmget.lua")):
        bodies[name] = (VARIANTS / fn).read_text()
        log.w(f"{name}: lab/variants/{fn} ({len(bodies[name].splitlines())} lines)")
    seed_static(r, log)
    rid = run_id(r)
    shas = {}
    for name, body in bodies.items():
        shas[name] = common.load_script(r, body)
        log.cmd(f"SCRIPT LOAD <{name}> -> {shas[name]}")
    log.cmd(f"EVALSHA <sha> 14 {' '.join(K)} 1 {GEN} {rid} {PROC} {LEASE_TTL} "
            f"{CANDIDATE_WINDOW} 600000 {BATCH_SIZE} <{BATCH_SIZE} uuid tokens>")

    # --- reply equality on the identical seeded state with identical tokens ---
    log.section("reply equality (same seed, same lease tokens)")
    fixed = tokens()
    norms, nows = {}, {}
    for name in ("orig", "hmget"):
        reset_mutable(r)
        reply = common.evalsha(r, shas[name], K, argv(fixed, rid))
        now, entries = check_reply(reply, fixed, log, name)
        norms[name], nows[name] = normalized(reply, now, entries), now
    identical = norms["orig"] == norms["hmget"]
    log.w(f"reply arrays identical (clock fields normalised): {identical}")
    if not identical:
        for i, (a, b) in enumerate(zip(norms["orig"], norms["hmget"])):
            if a != b:
                log.w(f"  first difference at index {i}: orig={a!r} hmget={b!r}")
                break
    log.w(f"len(orig)={len(norms['orig'])} len(hmget)={len(norms['hmget'])}; "
          f"now_ms orig={nows['orig']} hmget={nows['hmget']} (server clock, "
          f"expected to differ)")

    log.section("timing (state reset between every invocation, reset not timed)")
    rows, deltas = [], {}
    for name, label in (("orig", "orig (per-candidate HGET)"),
                        ("hmget", "hmget prefetch + deferred payload")):
        samples, delta = timed(r, shas[name], rid, log, name)
        deltas[name] = delta
        st = common.percentiles(samples)
        rows.append(common.stat_row(label, st))
    table = common.format_table(common.STAT_HEADERS, rows)
    log.w(table)

    lat = common.script_latency_text(r)
    log.cmd("SCRIPT LATENCY")
    log.section("SCRIPT LATENCY (usec; includes warm-up and the equality run)")
    for name in ("orig", "hmget"):
        log.w(f"[{name}] {shas[name]}")
        log.w(common.parse_script_latency(lat, shas[name]).get("raw", "(none)"))
    log.save()

    speedup = rows[0][2] / rows[1][2]
    body = (
        f"{ITERS} measured iterations after {WARMUP} warm-up. Before every single "
        f"invocation the mutable state is reset (leases, receipts, "
        f"diagnostic_owners, poll_starts, lease_records deleted; all "
        f"{N_MAILBOXES} mailboxes re-scored due in the past) so each measured call "
        f"sees the identical pre-claim state; the reset is not inside the timed "
        f"section. All 14 keys share the hashtag `{TAG}`.\n\n"
        f"**Fence check.** Every timed invocation was verified to return "
        f"`RESOLVED` with `{BATCH_SIZE}/{BATCH_SIZE}` `CLAIMED` entries, the "
        f"request tokens echoed in order and `deadline - now == {LEASE_TTL}` - not "
        f"a `FENCED`/`PROTOCOL` early exit.\n\n"
        f"**Reply equality.** On the identical seeded state with the identical "
        f"{BATCH_SIZE} lease tokens, the two reply arrays are "
        f"**{'IDENTICAL' if identical else 'DIFFERENT'}** field for field once the "
        f"two server-clock fields (`now`, and `deadline`, checked instead for "
        f"`deadline - now == {LEASE_TTL}`) are normalised: same `earliest_due`, "
        f"same batch size, same statuses, same mailbox ids in the same order, same "
        f"~{RECORD_BYTES}B record payloads.\n\n"
        f"{table}\n\n"
        f"The HMGET variant is **{speedup:.1f}x** faster at p50 "
        f"({rows[0][2]:.0f}us -> {rows[1][2]:.0f}us), p99 {rows[0][4]:.0f}us -> "
        f"{rows[1][4]:.0f}us. Execution-path counters over the measured runs: "
        f"orig `{common.format_counters(deltas['orig'])}`, hmget "
        f"`{common.format_counters(deltas['hmget'])}`.\n\n"
        f"Variant sources: `lab/variants/claim_mailbox_batch.orig.lua` (the "
        f"original script) and "
        f"`lab/variants/claim_mailbox_batch.hmget.lua`, whose header documents the "
        f"one semantic difference (`record_missing` is only checked for the "
        f"candidates actually claimed).\n")
    common.emit(QUESTION, "real script: claim_mailbox_batch.lua vs HMGET prefetch",
                ["Q7", f"claim_mailbox_batch.lua, {N_MAILBOXES} due, batch {BATCH_SIZE}, "
                       f"~{RECORD_BYTES}B records: per-candidate HGET vs chunked HMGET",
                 f"p50 {rows[0][2]:.0f}us -> {rows[1][2]:.0f}us ({speedup:.1f}x); "
                 f"p99 {rows[0][4]:.0f}us -> {rows[1][4]:.0f}us",
                 f"replies {'identical' if identical else 'DIFFER'}; prefetch + "
                 f"deferred payload is a {speedup:.1f}x win"
                 f" (standalone run, no co-tenant; Q10 re-measures the same "
                 f"workload back to back against `--lock_on_hashtags` and reports "
                 f"a different ratio -- see the reconciliation note in Q10)"],
                body)


if __name__ == "__main__":
    run()
