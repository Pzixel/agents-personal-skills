#!/usr/bin/env python3
"""SubagentStop hook (all worker types): a subagent does not end its turn in
order to wait for a background task.

Nothing resumes a subagent after its turn ends except a message from its
caller: the background-task notification is unreliable, and the harness kills
background Bash tasks with exit 144 at its own events. So a final reply that
says it is waiting, or a background task this agent launched and never
accounted for, returns the agent once with the foreground protocol; the second
stop (this hook's marker already in the transcript after the last launch) is
let through; `stop_hook_active` alone is not enough, since any SubagentStop
hook's block sets it. The final text is the payload's `last_assistant_message`
when present, else the last assistant text of the transcript.

Transcript conventions follow checks-before-done.py (`agent_transcript_path`,
one JSON entry per line). Decision: gate_policy.sleeping_stop_decision. Any
error exits 0 with no output.
"""

import datetime
import json
import os
import sys

# Plugin layout first (hooks/../tools), the ~/.claude install as fallback;
# logs and locks stay in ~/.claude whichever copy of the hook runs.
_PLUGIN_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
TOOLS_DIR = (
    _PLUGIN_TOOLS if os.path.isdir(_PLUGIN_TOOLS) else os.path.expanduser("~/.claude/tools")
)
STATE_DIR = os.path.expanduser("~/.claude")
sys.path.insert(0, TOOLS_DIR)

import gate_policy  # noqa: E402
from transcript import blocks_of, entries_of, launch_id  # noqa: E402

LOG_PATH = os.path.join(STATE_DIR, "sleeping-stop-log.jsonl")
# Tool calls that read or stop a background task account for it.
TASK_TOOLS = frozenset({"TaskOutput", "TaskStop", "BashOutput", "KillShell"})


def account_of(transcript_path):
    configured = os.environ.get("CLAUDE_CONFIG_DIR", "")
    if configured:
        return "work" if configured.rstrip("/").endswith("/work") else "default"
    if "/.claude-accounts/work/" in (transcript_path or ""):
        return "work"
    return "default"


def facts_of(entries):
    """Last assistant text, unresolved background ids, and whether this hook's
    marker appears after the last background launch."""
    launched = {}  # id -> index of the launch
    resolved = set()
    last_text = ""
    last_launch = -1
    marker_at = []
    for index, (raw, entry) in enumerate(entries):
        kind = entry.get("type")
        if kind == "assistant":
            texts = [str(b.get("text") or "") for b in blocks_of(entry) if b.get("type") == "text"]
            if any(t.strip() for t in texts):
                last_text = "\n".join(texts)
            for block in blocks_of(entry):
                if block.get("type") == "tool_use" and block.get("name") in TASK_TOOLS:
                    serialized = json.dumps(block.get("input") or {})
                    resolved.update(i for i in launched if i in serialized)
            continue
        if gate_policy.SLEEPING_STOP_MARKER in raw:
            marker_at.append(index)
        for block in blocks_of(entry):
            if block.get("type") != "tool_result":
                continue
            # Only the harness's own launch acknowledgement, which is the whole
            # result; a command output that quotes one is not a launch.
            task_id = launch_id(block)
            if task_id:
                launched[task_id] = index
                last_launch = index
        for task_id in launched:
            if launched[task_id] >= index:
                continue
            if "<task-id>{}</task-id>".format(task_id) in raw or (
                task_id in raw and "[exited with code" in raw
            ):
                resolved.add(task_id)
    unresolved = sorted(set(launched) - resolved)
    prior_block_seen = any(i > last_launch for i in marker_at)
    return last_text, unresolved, prior_block_seen


def log(record):
    try:
        record["ts"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with open(LOG_PATH, "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except BaseException:
        pass


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    payload = json.loads(raw)
    transcript = payload.get("agent_transcript_path") or ""
    if not transcript or not os.path.isfile(transcript):
        return 0

    last_text, unresolved, prior_block_seen = facts_of(entries_of(transcript))
    # The transcript may not hold the final reply yet; the payload's copy wins.
    if isinstance(payload.get("last_assistant_message"), str):
        last_text = payload["last_assistant_message"]
    stop_hook_active = bool(payload.get("stop_hook_active"))
    decision = gate_policy.sleeping_stop_decision(
        last_text, unresolved, stop_hook_active, prior_block_seen
    )
    log(
        {
            "account": account_of(transcript),
            "agent_type": payload.get("agent_type") or "",
            "agent_id": payload.get("agent_id") or "",
            "session_id": payload.get("session_id") or "",
            "unresolved": unresolved,
            "waiting_text": gate_policy.is_waiting_text(last_text),
            "stop_hook_active": stop_hook_active,
            "prior_block_seen": prior_block_seen,
            "decision": decision["decision"],
        }
    )
    if decision["decision"] == "block":
        json.dump({"decision": "block", "reason": decision["reason"]}, sys.stdout)
    return 0


try:
    main()
except BaseException:
    pass
sys.exit(0)
