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
# Escape hatch for all of these refusals: the literal
# AGENT_TYPE_OVERRIDE in the prompt. Any failure here exits 0 without output.
input=$(cat)
type=$(printf '%s' "$input" | jq -r '.tool_input.subagent_type // ""')
prompt=$(printf '%s' "$input" | jq -r '.tool_input.prompt // ""')
spawner=$(printf '%s' "$input" | jq -r '.agent_type // ""')
model=$(printf '%s' "$input" | jq -r '.tool_input.model // ""')
if printf '%s' "$prompt" | grep -q 'AGENT_TYPE_OVERRIDE'; then exit 0; fi

out=$(HOOK_PATH="$0" SPAWNER_TYPE="$spawner" REQUESTED_TYPE="$type" REQUESTED_MODEL="$model" REQUESTED_PROMPT="$prompt" python3 - <<'PY'
import json, os, sys

hook = os.path.realpath(os.environ.get("HOOK_PATH") or "")
tools = os.path.join(os.path.dirname(os.path.dirname(hook)), "tools")
# The plugin's own tools win; the ~/.claude install is the fallback.
for candidate in (os.path.expanduser("~/.claude/tools"), tools):
    if os.path.isdir(candidate):
        sys.path.insert(0, candidate)
import gate_policy

params = {}
for candidate in sys.path[:2]:
    path = os.path.join(candidate, "orchestrate-params.json")
    if os.path.isfile(path):
        with open(path) as fh:
            params = json.load(fh)
        break

spawner = os.environ.get("SPAWNER_TYPE", "")
requested = os.environ.get("REQUESTED_TYPE", "")
decision = gate_policy.spawn_decision(spawner, requested, params)
if decision["decision"] != "deny":
    decision = gate_policy.model_override_decision(
        spawner, requested, os.environ.get("REQUESTED_MODEL", ""), params
    )
if decision["decision"] != "deny":
    # A delegate's budget goes to the deliverable: the brief carries the pointers.
    decision = gate_policy.brief_decision(requested, os.environ.get("REQUESTED_PROMPT", ""))
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
