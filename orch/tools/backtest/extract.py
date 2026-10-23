"""Every Bash call in transcripts modified in the last 14 days: one JSONL row
{start_utc, duration_s, agent, agent_type, repo, command}; duration = tool_result
ts minus tool_use ts. repo = cwd cut before /.worktrees/ (worktrees pooled)."""
import json, os, sys, time, datetime, re
root = os.path.expanduser("~/.claude/projects"); cutoff = time.time() - 14 * 86400
out = open(sys.argv[1], "w"); n = 0
def ep(s): return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
cd_re = re.compile(r"""^\s*cd\s+("[^"]+"|'[^']+'|[^\s;|&]+)""")
for dp, _, fs in os.walk(root, followlinks=False):
    for f in fs:
        if not f.endswith(".jsonl"): continue
        p = os.path.join(dp, f)
        try:
            if os.path.getmtime(p) < cutoff: continue
        except OSError: continue
        sub = "/subagents/" in p
        agent = f[:-6] if sub else "main"
        uses = {}
        for line in open(p, errors="replace"):
            if '"Bash"' not in line and "tool_result" not in line: continue
            try: e = json.loads(line)
            except ValueError: continue
            msg = e.get("message") or {}
            c = msg.get("content")
            if not isinstance(c, list): continue
            for b in c:
                if not isinstance(b, dict): continue
                if b.get("type") == "tool_use" and b.get("name") == "Bash":
                    uses[b.get("id")] = (e.get("timestamp"), (b.get("input") or {}).get("command") or "", e.get("cwd") or "")
                elif b.get("type") == "tool_result" and b.get("tool_use_id") in uses:
                    ts, cmd, cwd = uses.pop(b["tool_use_id"])
                    try: d = ep(e["timestamp"]) - ep(ts)
                    except Exception: continue
                    m = cd_re.match(cmd)
                    d0 = m.group(1).strip("\"'") if m else cwd
                    d0 = os.path.expanduser(d0.replace("$HOME", os.path.expanduser("~")))
                    if not d0.startswith("/"): d0 = os.path.join(cwd, d0)
                    repo = d0.split("/.worktrees/")[0]
                    out.write(json.dumps({"start_utc": ts, "duration_s": round(d, 1), "agent": agent, "agent_type": "sub" if sub else "", "repo": repo, "command": cmd}) + "\n"); n += 1
print(n)
