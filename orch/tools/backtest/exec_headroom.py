"""Headroom-gate backtest: replay gate_policy's headroom decision over every
production `kubectl exec` of a calls.jsonl from extract.py, against one
cluster snapshot (limits and working sets now, not at the time of each exec).

  kubectl --context <prod-context> get pod -A -o json > pods.json
  kubectl --context <prod-context> top pod -A --containers --no-headers > top.txt
  python3 exec_headroom.py calls.jsonl pods.json top.txt [production-context] [default-context]

A target runs on production when its --context is the production context, or
it has none and the default context is production. A pod gone since the exec
is matched by its controller prefix (name minus the replica-set and pod
hashes, or minus the StatefulSet ordinal keeps the ordinal). Prints the denied
count by reason and the denied rows."""
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import gate_policy  # noqa: E402

calls, pods_path, top_path = sys.argv[1:4]
prod = sys.argv[4] if len(sys.argv) > 4 else os.environ.get("KUBE_PROD_CONTEXT", "")
default_ctx = sys.argv[5] if len(sys.argv) > 5 else prod
pods = {(p["metadata"]["namespace"], p["metadata"]["name"]): p for p in json.load(open(pods_path))["items"]}
top = collections.defaultdict(list)
for line in open(top_path):
    cols = line.split()
    if len(cols) >= 5:
        top[(cols[0], cols[1])].append(" ".join(cols[2:]))


def prefix(name):
    return re.sub(r"-[a-z0-9]{8,10}-[a-z0-9]{5}$|-[a-z0-9]{5}$", "", name)


by_prefix = {}
for ns, name in pods:
    by_prefix.setdefault((ns, prefix(name)), name)

counts, denied, total = collections.Counter(), [], 0
for line in open(calls):
    row = json.loads(line)
    if "exec" not in row["command"] or "kubectl" not in row["command"] and "k " not in row["command"]:
        continue
    try:
        targets = gate_policy.kubectl_exec_targets(row["command"])
    except Exception:
        continue
    targets = [t for t in targets if (t["context"] or default_ctx) == prod]
    if not targets:
        continue
    total += 1
    if gate_policy.headroom_acknowledged(row["command"]):
        counts["acknowledged"] += 1
        continue
    verdict = "allow"
    for t in targets:
        adds = gate_policy.headroom_adds(t["inner"], None, t.get("stdin"))
        ns = t["namespace"] or "default"
        name = t["pod"] if (ns, t["pod"]) in pods else by_prefix.get((ns, prefix(t["pod"])))
        if adds[0] is None:
            verdict = "deny: JVM no heap cap"
        elif name is None:
            counts["unknown: pod not in snapshot"] += 1
            continue
        else:
            t = dict(t, pod=name)
            facts = gate_policy.headroom_facts(t, json.dumps(pods[(ns, name)]), "\n".join(
                name + " " + c for c in top[(ns, name)]))
            result = gate_policy.headroom_decision(t, adds, facts)
            if result["decision"] == "deny":
                verdict = "deny: JVM heap > free" if "JVM" in adds[1] else "deny: floor > free"
            elif result["context"] and "unknown" in result["context"]:
                counts["unknown: " + result["context"].split("(")[1].split(")")[0].split(" ")[0]] += 1
        if verdict != "allow":
            break
    counts[verdict] += 1
    if verdict != "allow":
        denied.append((row["start_utc"], verdict, row["command"][:160].replace("\n", " ")))

print("production exec commands:", total)
for key, n in sorted(counts.items()):
    print("%-40s %5d  %.2f%%" % (key, n, 100.0 * n / max(total, 1)))
for d in denied:
    print("\t".join(d))
