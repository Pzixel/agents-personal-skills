#!/usr/bin/env python3
"""Cut a Claude Code transcript into a `context.history_file` for an eval case.

usage: cut_history.py <transcript.jsonl> <start_line> <end_line> <out.jsonl>

Keeps the main-chain `user`/`assistant` records of lines start..end (1-based,
inclusive; start at a `compact_boundary` line to begin from its summary),
drops sidechain, attachment and bookkeeping records and `thinking` blocks
(their signatures belong to the source session), and relinks `parentUuid`
into one linear chain. Fails when the cut ends on a tool_use without its
result or on anything but a user/assistant message.
"""
import json
import sys

src, start, end, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
kept = []
with open(src, errors="ignore") as f:
    for no, line in enumerate(f, 1):
        if no < start or no > end:
            continue
        rec = json.loads(line)
        if rec.get("type") not in ("user", "assistant") or rec.get("isSidechain"):
            continue
        msg = rec.get("message") or {}
        if isinstance(msg.get("content"), list):
            msg["content"] = [b for b in msg["content"] if b.get("type") not in ("thinking", "redacted_thinking")]
            if not msg["content"]:
                continue
        kept.append(rec)

if not kept:
    sys.exit("nothing kept")
open_uses = set()
for rec in kept:
    for b in rec["message"]["content"] if isinstance(rec["message"].get("content"), list) else []:
        if b.get("type") == "tool_use":
            open_uses.add(b["id"])
        elif b.get("type") == "tool_result":
            open_uses.discard(b.get("tool_use_id"))
if open_uses:
    sys.exit(f"cut leaves {len(open_uses)} tool_use without result; move end_line")

parent = None
with open(out, "w") as f:
    for rec in kept:
        rec["parentUuid"] = parent
        parent = rec["uuid"]
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
print(f"{len(kept)} records -> {out}")
