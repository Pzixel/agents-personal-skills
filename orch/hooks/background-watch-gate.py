#!/usr/bin/env python3
"""Stop hook (main session): a turn does not end with a background Bash task
of this session open and unwatched.

The harness notifies only when a whole background command exits, so a failed
child, a stall or an hours-long run is silent until the owner returns. The
stop is returned once when an open task was launched this turn, or its last
Monitor ended this turn, and no Monitor is live on it and no CronCreate
follows it. The second stop passes on this hook's marker in the transcript;
`stop_hook_active` alone is not enough, since reply-lint also blocks on Stop.
Facts: transcript.background_watch_scan; decision:
gate_policy.background_watch_decision. Any error exits 0 with no output.
"""

import datetime
import json
import os
import sys

_PLUGIN_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
TOOLS_DIR = (
    _PLUGIN_TOOLS if os.path.isdir(_PLUGIN_TOOLS) else os.path.expanduser("~/.claude/tools")
)
sys.path.insert(0, TOOLS_DIR)

import gate_policy  # noqa: E402
import transcript  # noqa: E402

LOG_PATH = os.path.join(os.path.expanduser("~/.claude"), "background-watch-log.jsonl")


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
    # Subagents have sleeping-stop on SubagentStop.
    if payload.get("agent_type") or payload.get("agent_id"):
        return 0
    path = payload.get("transcript_path") or ""
    if not path or not os.path.isfile(path):
        return 0
    entries = transcript.entries_of(path)
    facts = None
    for facts in transcript.background_watch_scan(entries, gate_policy.BACKGROUND_WATCH_MARKER):
        pass
    stop_hook_active = bool(payload.get("stop_hook_active"))
    decision = gate_policy.background_watch_decision(
        facts, stop_hook_active, facts["prior_block_seen"]
    )
    log(
        {
            "session_id": payload.get("session_id") or "",
            "decision": decision["decision"],
            "ids": decision["ids"],
            "waits": decision["waits"],
            "stop_hook_active": stop_hook_active,
            "prior_block_seen": facts["prior_block_seen"],
        }
    )
    if decision["decision"] == "block":
        json.dump({"decision": "block", "reason": decision["reason"]}, sys.stdout)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
