#!/usr/bin/env python3
"""UserPromptSubmit hook: the owner approves one destructive plan run.

A prompt line `APPROVE <sha256 hex>` creates the empty file
<approvals_dir>/<session_id>/<hash>; deny-destructive.py allows the matching
DESTRUCTIVE_PLAN run once and removes the file. UserPromptSubmit fires only
for a prompt the owner typed, never for agent output: that is the trust
boundary. Agents cannot write approvals_dir (deny-destructive refuses it).
The hook never blocks the prompt.
"""
import json
import os
import sys

def main():
    payload = json.load(sys.stdin)
    session = str(payload.get("session_id") or "")
    tools = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
    sys.path.insert(0, tools if os.path.isdir(tools) else os.path.expanduser("~/.claude/tools"))
    import gate_policy

    hashes = gate_policy.approved_hashes(str(payload.get("prompt") or ""))
    if not hashes or not gate_policy.session_id_ok(session):
        return
    root = gate_policy.approvals_root(gate_policy.load_params(), os.path.expanduser("~"))
    folder = os.path.join(root, session)
    os.makedirs(folder, exist_ok=True)
    for h in hashes:
        open(os.path.join(folder, h), "a").close()
        print("plan %s approved for this session, one run" % h[:12])


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("plan-approval hook error: %s: %s" % (type(exc).__name__, exc))
    sys.exit(0)
