#!/usr/bin/env python3
"""PreToolUse hook (Bash): route long output of listed agent types to a file.

Mechanical only: resolves the agent type, picks the output path, asks
gate_policy.bash_clamp_decision and emits updatedInput. Any error exits 0.
"""

import json
import os
import sys

_PLUGIN_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
TOOLS_DIR = os.path.realpath(
    _PLUGIN_TOOLS if os.path.isdir(_PLUGIN_TOOLS) else os.path.expanduser("~/.claude/tools")
)
sys.path.insert(0, TOOLS_DIR)

import gate_policy  # noqa: E402


def agent_type_of(payload):
    raw = payload.get("agent_type") or ""
    if raw:
        return raw
    own = gate_policy.own_transcript_path(
        payload.get("transcript_path") or "", payload.get("agent_id") or ""
    )
    if not own:
        return ""
    meta = own[: -len(".jsonl")] + ".meta.json"
    with open(meta) as fh:
        return json.load(fh).get("agentType") or ""


def agent_dir_name(payload):
    own = gate_policy.own_transcript_path(
        payload.get("transcript_path") or "", payload.get("agent_id") or ""
    )
    return os.path.basename(own)[: -len(".jsonl")] or "session"


def main():
    payload = json.loads(sys.stdin.read())
    command = str((payload.get("tool_input") or {}).get("command") or "")
    agent_type = agent_type_of(payload)
    if not command or not agent_type:
        return 0
    with open(os.path.join(TOOLS_DIR, "orchestrate-params.json")) as fh:
        params = json.load(fh)
    root = os.environ.get("CLAUDE_CODE_TMPDIR") or os.environ.get("TMPDIR")
    if not root:
        return 0
    out_dir = os.path.join(root, "orch-out", agent_dir_name(payload))
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "%d.log" % (len(os.listdir(out_dir)) + 1))
    wrapped = gate_policy.bash_clamp_decision(agent_type, command, params, out_path, TOOLS_DIR)
    if wrapped is None:
        return 0
    tool_input = dict(payload.get("tool_input") or {})
    tool_input["command"] = wrapped
    json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                      "updatedInput": tool_input}}, sys.stdout)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
