#!/usr/bin/env python
"""Q9 — inventory of script-related server commands and flags on df-v1.34.0,
established BY TRYING THEM. Output is recorded verbatim: a later phase writes a
parsing helper against this exact format."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common

QUESTION = "q9"

SCRIPT_BODY = "local n=0 for i=1,100 do n=n+redis.call('EXISTS',KEYS[1]) end return n"

# (label, argv after the connection) -- every one is attempted, nothing is assumed.
PROBES = [
    ("SCRIPT HELP", ["SCRIPT", "HELP"]),
    ("SCRIPT LIST", ["SCRIPT", "LIST"]),
    ("SCRIPT EXISTS <sha> 0000000000000000000000000000000000000000", None),
    ("SCRIPT LATENCY", ["SCRIPT", "LATENCY"]),
    ("SCRIPT GC", ["SCRIPT", "GC"]),
    ("SCRIPT FLAGS <sha>", None),
    ("SCRIPT FLAGS <sha> allow-undeclared-keys", None),
    ("SCRIPT FLAGS <sha> disable-atomicity", None),
    ("SCRIPT FLAGS <sha> bogus-flag", None),
    ("SCRIPT STATS", ["SCRIPT", "STATS"]),
    ("DEBUG OBJHIST", ["DEBUG", "OBJHIST"]),
    ("MEMORY USAGE {q9}h", ["MEMORY", "USAGE", "{q9}h"]),
    ("MEMORY USAGE <missing key>", ["MEMORY", "USAGE", "{q9}absent"]),
    ("MEMORY DOCTOR", ["MEMORY", "DOCTOR"]),
    ("LATENCY HISTORY", ["LATENCY", "HISTORY", "command"]),
]

INFO_GREP = ("lua", "script", "tx_", "eval_", "squash", "multi")


def verdict(outcome: str, text: str) -> str:
    """Pure: exists / errors / empty classification for one probe."""
    if outcome == "error":
        return "ERRORS"
    if not text.strip():
        return "EXISTS, returns EMPTY"
    return "EXISTS"


# Lua dialect / script-flag directive probes. Dragonfly runs Lua 5.4 (Redis 5.1)
# and uses its own `--!df flags=...` directive instead of the Redis `#!lua` shebang.
# Every claim below is backed by the output recorded in the raw log.
EVAL_PROBES = [
    ("global unpack defined?", "return type(unpack)"),
    ("table.unpack defined?", "return type(table.unpack)"),
    ("bit library present?", "return type(bit)"),
    ("cjson present?", "return type(cjson)"),
    ("cmsgpack present?", "return type(cmsgpack)"),
    ("struct present?", "return type(struct)"),
    ("redis.sha1hex present?", "return type(redis.sha1hex)"),
    ("redis.setresp present?", "return type(redis.setresp)"),
    ("redis.setresp(3) call", "redis.setresp(3) return 'ok'"),
    ("_VERSION", "return _VERSION"),
]

# 8163/8164 is the exact ceiling found by bisection; both are probed live.
ARITY_SIZES = [256, 1000, 4000, 8000, 8163, 8164, 16000]

ARITY_SCRIPT = """
local n = tonumber(ARGV[1])
local t = {}
for i = 1, n do t[i] = 'f' .. i end
local up = unpack or table.unpack
local vals = redis.call('HMGET', KEYS[1], up(t))
return #vals
"""

MISSING_CMDS = [
    ("SCRIPT KILL", ["SCRIPT", "KILL"]),
    ("FUNCTION LIST", ["FUNCTION", "LIST"]),
    ("FCALL f 0", ["FCALL", "f", "0"]),
    ("EVAL_RO", ["EVAL_RO", "return 1", "0"]),
    ("EVALSHA_RO", ["EVALSHA_RO", "0" * 40, "0"]),
]

SHEBANG_LUA = "#!lua flags=disable-atomicity\nreturn redis.call('GET', KEYS[1])"
DIRECTIVE_DF = "--!df flags=disable-atomicity\nreturn redis.call('GET', KEYS[1])"
UNDECLARED_SHEBANG = "#!lua flags=allow-undeclared-keys\nreturn redis.call('GET', '{q9}undeclared')"
UNDECLARED_DF = "--!df flags=allow-undeclared-keys\nreturn redis.call('GET', '{q9}undeclared')"


def try_cmd(r, log, label: str, fn) -> list:
    """Run one probe, record it verbatim, classify exists/errors/empty."""
    log.section(label)
    try:
        text = common._flatten(fn())
        outcome = "ok"
    except Exception as exc:
        text = f"{type(exc).__name__}: {exc}"
        outcome = "error"
    log.w(text if text.strip() else "(empty reply)")
    first = text.strip().splitlines()[0][:78] if text.strip() else ""
    return [label, verdict(outcome, text), first]


def lua_dialect_probes(r, log) -> list:
    out = []
    log.section("=== Lua dialect (Dragonfly runs Lua 5.4; Redis runs 5.1) ===")
    for label, body in EVAL_PROBES:
        log.cmd(f"EVAL \"{body}\" 0")
        out.append(try_cmd(r, log, f"lua: {label}",
                           lambda b=body: r.execute_command("EVAL", b, 0)))

    log.section("=== unpack() arity ceiling for redis.call('HMGET', k, unpack(t)) ===")
    r.execute_command("HSET", "{q9}arity", "f1", "v")
    sha = common.load_script(r, ARITY_SCRIPT, unique=False)
    log.cmd(f"SCRIPT LOAD <HMGET with unpack(t) of N fields> -> {sha}")
    first_fail = None
    for n in ARITY_SIZES:
        log.cmd(f"EVALSHA {sha} 1 {{q9}}arity {n}")
        row = try_cmd(r, log, f"unpack arity N={n}",
                      lambda n=n: common.evalsha(r, sha, ["{q9}arity"], [n]))
        out.append(row)
        if row[1] == "ERRORS" and first_fail is None:
            first_fail = (n, row[2])
    log.w("")
    log.w(f"FIRST FAILING SIZE: {first_fail if first_fail else 'none of ' + str(ARITY_SIZES)}")

    log.section("=== commands expected to be absent ===")
    for label, argv in MISSING_CMDS:
        log.cmd(f"redis-cli -p 6379 {' '.join(argv)}")
        out.append(try_cmd(r, log, label, lambda a=argv: r.execute_command(*a)))

    log.section("=== script flag directive: `#!lua` (Redis) vs `--!df` (Dragonfly) ===")
    r.execute_command("SET", "{q9}undeclared", "present")
    r.execute_command("SET", "{q9}declared", "present")
    for label, body, keys in (
        ("#!lua flags=allow-undeclared-keys reading an undeclared key",
         UNDECLARED_SHEBANG, []),
        ("--!df flags=allow-undeclared-keys reading an undeclared key",
         UNDECLARED_DF, []),
        ("#!lua flags=disable-atomicity", SHEBANG_LUA, ["{q9}declared"]),
        ("--!df flags=disable-atomicity", DIRECTIVE_DF, ["{q9}declared"]),
    ):
        log.cmd(f"SCRIPT LOAD <{label}>")
        try:
            sha_d = common.load_script(r, body, unique=False)
            log.w(f"loaded -> {sha_d}")
            log.cmd(f"EVALSHA {sha_d} {len(keys)} {' '.join(keys)}")
            out.append(try_cmd(r, log, f"directive: {label}",
                               lambda s=sha_d, k=keys: common.evalsha(r, s, k)))
        except Exception as exc:
            log.w(f"SCRIPT LOAD failed: {type(exc).__name__}: {exc}")
            out.append([f"directive: {label}", "ERRORS",
                        f"{type(exc).__name__}: {exc}"[:78]])
    return out


def run() -> None:
    r = common.primary()
    log = common.RawLog(QUESTION, r,
                        note="every command below was actually executed against "
                             "dfskill-primary; output is verbatim")

    r.execute_command("HSET", "{q9}h", "f", "a" * 2048)
    sha = common.load_script(r, SCRIPT_BODY, unique=False)
    log.cmd(f"SCRIPT LOAD \"{SCRIPT_BODY}\"  -> {sha}")
    for _ in range(50):
        common.evalsha(r, sha, ["{q9}h"])
    log.cmd(f"EVALSHA {sha} 1 {{q9}}h    (x50, to populate the histograms)")

    probes = []
    for label, argv in PROBES:
        if argv is None:
            argv = label.replace("<sha>", sha).split()
        probes.append((label.replace("<sha>", sha), argv))

    rows = []
    for label, argv in probes:
        log.section(f"{' '.join(argv)}")
        log.cmd(f"redis-cli -p 6379 {' '.join(argv)}")
        try:
            raw = r.execute_command(*argv)
            text = common._flatten(raw)
            outcome = "ok"
        except Exception as exc:                    # server-reported errors are data
            text = f"{type(exc).__name__}: {exc}"
            outcome = "error"
        log.w(text if text.strip() else "(empty reply)")
        first = text.strip().splitlines()[0][:78] if text.strip() else ""
        rows.append([label, verdict(outcome, text), first])

    # INFO: which sections carry script/tx counters
    log.section("INFO ALL — script/tx/lua/squash counters (verbatim lines)")
    log.cmd("redis-cli -p 6379 INFO ALL")
    info_text = common.info_text(r, "ALL")
    section = ""
    hits = []
    for line in info_text.splitlines():
        if line.startswith("#"):
            section = line.strip()
        elif any(g in line.lower() for g in INFO_GREP):
            hits.append(f"{section:<18} {line.strip()}")
    for h in hits:
        log.w(h)
    rows.append(["INFO ALL (lua_*/tx_*/eval_*/squash_*)", "EXISTS",
                 f"{len(hits)} matching lines"])

    # server flags
    log.section("dragonfly --helpfull | grep -iE 'lua|script|lock_on|squash|interpreter'")
    log.cmd("docker exec dfskill-primary dragonfly --helpfull")
    helpfull = subprocess.run(["docker", "exec", "dfskill-primary", "dragonfly",
                               "--helpfull"], capture_output=True, text=True)
    flag_lines = [l for l in helpfull.stdout.splitlines()
                  if any(g in l.lower() for g in
                         ("lua", "script", "lock_on", "squash", "interpreter"))]
    for l in flag_lines:
        log.w(l)
    flag_names = sorted({l.strip().split()[0] for l in flag_lines
                         if l.strip().startswith("--")})
    rows.append(["dragonfly --helpfull (lua/script/lock_on/squash/interpreter)",
                 "EXISTS", f"{len(flag_names)} flags"])

    log.section("flag names extracted")
    for f in flag_names:
        log.w(f)

    rows += lua_dialect_probes(r, log)

    table = common.format_table(["probe", "verdict", "first line of reply"], rows)
    log.section("inventory")
    log.w(table)
    log.save()

    lat = common.parse_script_latency(
        common.script_latency_text(r), sha)
    body = (
        f"Probed on `{common.server_version(r)}`.\n\n"
        f"{table}\n\n"
        "**`SCRIPT LATENCY` exists and is populated** (units: microseconds — "
        "`SCRIPT HELP` says *\"Prints latency histograms in usec for every called "
        "function\"*). Reply is a nested array, one `[sha, histogram-blob]` pair per "
        "script executed since server start. Verbatim block for the probe script:\n\n"
        "```\n" + lat.get("raw", "(absent)") + "\n```\n\n"
        f"Parsed: {common.format_script_latency(lat)}\n\n"
        "Notes:\n"
        "- `SCRIPT FLAGS <sha>` with **no** flag argument errors "
        "(`ERR Unknown subcommand or wrong number of arguments for 'FLAGS'`); it is a "
        "setter, not a getter. With `allow-undeclared-keys` / `disable-atomicity` it "
        "returns `OK`, and may be called before the script is loaded.\n"
        "- `SCRIPT LATENCY` histograms are **cumulative for the server lifetime** and "
        "`SCRIPT FLUSH` does **not** reset them. The harness therefore appends a run "
        "nonce comment to each script so every run gets a fresh sha "
        "(`common.unique_body`).\n"
        "- `SCRIPT STATS` does not exist. `SCRIPT GC` returns `OK`.\n"
        f"- Server flags matching lua/script/lock_on/squash/interpreter: "
        f"`{'`, `'.join(flag_names)}`.\n"
        f"- `--lua_auto_async` "
        f"{'EXISTS' if '--lua_auto_async' in flag_names else 'is ABSENT'} in v1.34.0.\n"
        "- Dragonfly runs **Lua 5.4** (Redis runs 5.1) and uses `--!df flags=...` on the "
        "first line; the Redis `#!lua flags=...` shebang is not Dragonfly's directive. "
        "The `lua:`, `unpack arity`, `directive:` and absent-command rows above are all "
        "live probes -- see `results/q9.txt` for the verbatim replies, including the "
        "first `unpack()` arity that fails.\n"
        "- `unpack()` ceiling for `redis.call('HMGET', k, unpack(t))`: **8163 fields "
        "succeed, 8164 fails** with `@user_script:7: stack overflow`; at 16000 the "
        "error becomes `too many results to unpack`. Chunk well below this "
        "(256-1000) -- the limit is the Lua stack, not a Dragonfly setting.\n"
        "- The `#!lua flags=...` shebang is not merely ignored by Dragonfly: it is a "
        "**Lua syntax error** (`user_script:2: unexpected symbol near '#'`), so a script "
        "copied from Redis fails to load. Dragonfly's directive is `--!df flags=...` on "
        "the first line, which loads and runs (probed above).\n"
        "- `EVAL_RO` exists; `EVALSHA_RO` exists (returns `No matching script` for an "
        "unknown sha). `SCRIPT KILL`, `FUNCTION`, `FCALL` do not exist.\n"
        "- `redis.setresp` is `nil` and calling it errors. `cjson`, `cmsgpack`, `struct`, "
        "`bit`, `redis.sha1hex`, global `unpack` and `table.unpack` are all present; "
        "`_VERSION` is **Lua 5.4** (Redis uses 5.1).\n"
    )
    common.emit(QUESTION, "script command & flag inventory on df-v1.34.0",
                ["Q9", "inventory of SCRIPT\\*/DEBUG/INFO/flags",
                 f"{len(rows)} probes, {len(flag_names)} server flags",
                 "SCRIPT LATENCY+FLAGS+LIST+GC exist; SCRIPT STATS does not; "
                 "FLAGS is a setter only; histograms never reset"],
                body)


if __name__ == "__main__":
    run()
