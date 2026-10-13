#!/bin/bash
# PreToolUse hook for the Agent tool: refuse untyped subagents and enforce spawn rights.
# general-purpose / claude carry ~45k tokens of system prompt per turn (measured 2026-09-15);
# typed agents in ~/.claude/agents carry ~16k. Spawn rights are keyed by the SPAWNING agent's
# type (absent in the main session) and live in tools/orchestrate-params.json; the decision
# itself is gate_policy.spawn_decision. A spawner also may not pick the spawned agent's
# model (tool_input.model): that comes from the agent definition, exceptions are listed
# per triple in `model_override_rights` (gate_policy.model_override_decision).
# A builder prompt also needs `files:` and `check:` lines and a prod-probe prompt an
# `access:` line (gate_policy.brief_decision): discovery stays with the caller.
# Escape hatch for all of these refusals, main session only: a prompt line
# `AGENT_TYPE_OVERRIDE: <reason>` (gate_policy.override_marker), logged to
# ~/.claude/agent-type-overrides.jsonl. A subagent carrying it is refused.
# Any failure here exits 0 without output.
input=$(cat)
type=$(printf '%s' "$input" | jq -r '.tool_input.subagent_type // ""')
prompt=$(printf '%s' "$input" | jq -r '.tool_input.prompt // ""')
spawner=$(printf '%s' "$input" | jq -r '.agent_type // ""')
model=$(printf '%s' "$input" | jq -r '.tool_input.model // ""')
session=$(printf '%s' "$input" | jq -r '.session_id // ""')

out=$(SESSION_ID="$session" HOOK_PATH="$0" SPAWNER_TYPE="$spawner" REQUESTED_TYPE="$type" REQUESTED_MODEL="$model" REQUESTED_PROMPT="$prompt" python3 - <<'PY'
import json, os, sys

hook = os.path.realpath(os.environ.get("HOOK_PATH") or "")
tools = os.path.join(os.path.dirname(os.path.dirname(hook)), "tools")
# The plugin's own tools win; the ~/.claude install is the fallback.
for candidate in (os.path.expanduser("~/.claude/tools"), tools):
    if os.path.isdir(candidate):
        sys.path.insert(0, candidate)
import gate_policy

params = gate_policy.load_params()
spawner = os.environ.get("SPAWNER_TYPE", "")
requested = os.environ.get("REQUESTED_TYPE", "")
model = os.environ.get("REQUESTED_MODEL", "")
decision = gate_policy.spawn_gate_decision(
    spawner, requested, model, os.environ.get("REQUESTED_PROMPT", ""), params
)
if decision["decision"] == "override":
    import datetime
    record = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "session_id": os.environ.get("SESSION_ID", ""),
        "spawner": "main",
        "subagent_type": requested,
        "model": model,
        "reason": decision["reason"],
    }
    with open(os.path.expanduser("~/.claude/agent-type-overrides.jsonl"), "a") as fh:
        fh.write(json.dumps(record) + "\n")
if decision["decision"] == "deny":
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": decision["reason"],
            }
        },
        sys.stdout,
    )
PY
) || exit 0
[ -n "$out" ] && printf '%s' "$out"
exit 0
