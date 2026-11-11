#!/usr/bin/env python3
"""UserPromptSubmit hook: the owner approves one destructive plan run.

A prompt line `APPROVE <sha256 hex>` creates the empty file
<approvals_dir>/<session_id>/<hash>; deny-destructive.py allows the matching
DESTRUCTIVE_PLAN run once and removes the file. UserPromptSubmit also fires
for prompts the harness submits (a task notification, a scheduled job's or a
/loop wakeup's prompt), which may quote agent text: only a prompt that
chat_policy.typed_by_owner recognises over the session transcript approves; a
prompt with no readable transcript approves nothing (CASE-80). Agents cannot
write approvals_dir (deny-destructive refuses it). The hook never blocks the
prompt.
"""
import json
import os
import sys

def main():
    payload = json.load(sys.stdin)
    session = str(payload.get("session_id") or "")
    tools = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
    sys.path.insert(0, tools if os.path.isdir(tools) else os.path.expanduser("~/.claude/tools"))
    import chat_policy
    import gate_policy
    import transcript

    prompt = str(payload.get("prompt") or "")
    hashes = gate_policy.approved_hashes(prompt)
    source = str(payload.get("transcript_path") or "")
    if not hashes or not gate_policy.session_id_ok(session) or not os.path.isfile(source):
        return
    facts = None
    for facts in transcript.background_watch_scan(transcript.entries_of(source), ""):
        pass
    if not chat_policy.typed_by_owner(prompt, facts):
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
