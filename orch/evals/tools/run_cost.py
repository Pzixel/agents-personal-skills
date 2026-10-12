#!/usr/bin/env python3
"""True cost of one eval run from its --keep-temp dir (chmod 700 <dir> <dir>/sealed first).

Sums the main and every subagent transcript under <dir>/config/projects, usage
deduplicated per message id, priced per model family; prints them beside the
harness `total_cost_usd` (out/trace.jsonl). Usage: run_cost.py <dir> [...]
"""
import glob
import json
import os
import sys

# $/MTok: input, output, cache write 5m, cache write 1h, cache read
# (list prices, same table as ~/.claude/usage-audit.py; opus row = Opus 5.5, 2026-09-30).
RATES = {"fable": (10.0, 50.0, 12.50, 20.0, 0.25), "opus": (4.0, 20.0, 5.0, 8.0, 0.20),
         "sonnet": (2.0, 10.0, 2.50, 4.0, 0.20), "haiku": (1.0, 5.0, 1.25, 2.0, 0.10)}


def family(model):  # same default as usage-audit.py
    return next((k for k in RATES if k in (model or "")), "opus")


def usage_cost(u, model):
    r = RATES[family(model)]
    cc = u.get("cache_creation") or {}
    w5, w1 = cc.get("ephemeral_5m_input_tokens"), cc.get("ephemeral_1h_input_tokens")
    if w5 is None and w1 is None:
        w5, w1 = u.get("cache_creation_input_tokens", 0) or 0, 0
    return (u.get("input_tokens", 0) * r[0] + u.get("output_tokens", 0) * r[1]
            + (w5 or 0) * r[2] + (w1 or 0) * r[3]
            + u.get("cache_read_input_tokens", 0) * r[4]) / 1e6


def transcript_cost(path):
    by_id = {}
    with open(path) as fh:
        for line in fh:
            rec = json.loads(line)
            msg = rec.get("message") or {}
            if rec.get("type") != "assistant" or not msg.get("usage"):
                continue
            key = msg.get("id") or id(rec)
            old = by_id.get(key)
            if old is None or msg["usage"].get("output_tokens", 0) >= old[0].get("output_tokens", 0):
                by_id[key] = (msg["usage"], msg.get("model"))
    usd = sum(usage_cost(u, m) for u, m in by_id.values())
    out = sum(u.get("output_tokens", 0) for u, _ in by_id.values())
    return usd, out, sorted({family(m) for _, m in by_id.values()})


def harness_cost(run_dir):
    trace = os.path.join(run_dir, "out", "trace.jsonl")
    with open(trace) as fh:
        for line in fh:
            rec = json.loads(line)
            if rec.get("type") == "result":
                return rec.get("total_cost_usd")
    return None


def main(dirs):
    for run_dir in dirs:
        files = sorted(glob.glob(os.path.join(run_dir, "config", "projects", "*", "*.jsonl"))
                       + glob.glob(os.path.join(run_dir, "config", "projects", "*", "*",
                                                "subagents", "*.jsonl")))
        total = 0.0
        for path in files:
            usd, out, models = transcript_cost(path)
            total += usd
            kind = "subagent" if "/subagents/" in path else "main"
            print("  %-8s %-28s %8.3f $  out %7d  %s"
                  % (kind, os.path.basename(path)[:28], usd, out, ",".join(models)))
        print("%s  true %.3f $  harness total_cost_usd %s  (%d transcripts)"
              % (run_dir, total, harness_cost(run_dir), len(files)))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
