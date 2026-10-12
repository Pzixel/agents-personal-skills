#!/usr/bin/env python3
"""PreToolUse hook: budget a subagent by the cost of its own context.

Mechanical only. It reads the PreToolUse payload, measures three facts from
the agent's own transcript -- current context (input + cache read + cache
creation of the last assistant turn), cumulative cost, tool-call count --
hands them to gate_policy.agent_budget_decision, and emits the response.

Work agents (builder/reviewer) are budgeted by
context and cost; search/probe agents keep call caps. Thresholds live in
tools/orchestrate-params.json; gate_policy holds identical defaults, so a
missing file changes nothing.

On a stop, two structured exits stay open on every later call: a Write to any
*.md path, and (for the types that own a task branch) a bare handoff.sh call
validated by handoff-only.py. Any error at all exits 0: this hook never stands
between an agent and its work because something was unparseable.
"""

import json
import os
import re
import subprocess
import sys

HOOK_DIR = os.path.dirname(os.path.realpath(__file__))
# Plugin layout first (hooks/../tools), the ~/.claude install as fallback;
# logs and locks stay in ~/.claude whichever copy of the hook runs.
_PLUGIN_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
TOOLS_DIR = (
    _PLUGIN_TOOLS if os.path.isdir(_PLUGIN_TOOLS) else os.path.expanduser("~/.claude/tools")
)
STATE_DIR = os.path.expanduser("~/.claude")
sys.path.insert(0, TOOLS_DIR)

import gate_policy  # noqa: E402

PARAMS_PATH = os.path.join(TOOLS_DIR, "orchestrate-params.json")

# $/MTok: input, output, cache write 5m, cache write 1h, cache read.
PRICES = {
    "fable": (10.0, 50.0, 12.5, 20.0, 0.25),
    "opus": (4.0, 20.0, 5.0, 8.0, 0.20),
    "sonnet": (2.0, 10.0, 2.5, 4.0, 0.20),
    "haiku": (1.0, 5.0, 1.25, 2.0, 0.10),
}
DEFAULT_PRICE = PRICES["opus"]

COMMIT_TYPES = ("builder",)
_OWN_TRANSCRIPT = re.compile(r"/subagents/agent-[^/]+\.jsonl$")


def load_params():
    try:
        with open(PARAMS_PATH) as fh:
            return json.load(fh)
    except Exception:
        return {}


def resolve_transcript(transcript, agent_id):
    """The harness hands subagent hooks the PARENT session transcript (observed
    2026-09-16). Resolve the subagent's own file; if it cannot be found, do not
    charge the parent's context to this agent."""
    if not transcript:
        return ""
    if _OWN_TRANSCRIPT.search(transcript):
        return transcript if os.path.isfile(transcript) else ""
    if not agent_id:
        return ""
    own = os.path.join(
        os.path.dirname(transcript),
        os.path.basename(transcript)[: -len(".jsonl")]
        if transcript.endswith(".jsonl")
        else os.path.basename(transcript),
        "subagents",
        "agent-%s.jsonl" % agent_id,
    )
    return own if os.path.isfile(own) else ""


def price_for(model):
    name = (model or "").lower()
    for key, price in PRICES.items():
        if key in name:
            return price
    return DEFAULT_PRICE


def measure(path):
    """(context_tokens, cost_usd, tool_calls) from an agent transcript."""
    seen = set()
    cost = 0.0
    calls = 0
    context = 0
    with open(path, errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or '"assistant"' not in line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("type") != "assistant":
                continue
            msg = rec.get("message") or {}
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    calls += 1
            usage = msg.get("usage") or {}
            if not usage:
                continue
            msg_id = msg.get("id")
            if msg_id is not None and msg_id in seen:
                continue
            if msg_id is not None:
                seen.add(msg_id)
            creation = usage.get("cache_creation") or {}
            w5 = int(creation.get("ephemeral_5m_input_tokens") or 0)
            w1h = int(creation.get("ephemeral_1h_input_tokens") or 0)
            total_write = int(usage.get("cache_creation_input_tokens") or 0)
            if not w5 and not w1h:
                w5 = total_write
            read = int(usage.get("cache_read_input_tokens") or 0)
            inp = int(usage.get("input_tokens") or 0)
            out = int(usage.get("output_tokens") or 0)
            p_in, p_out, p_w5, p_w1h, p_read = price_for(msg.get("model"))
            cost += (
                inp * p_in + out * p_out + w5 * p_w5 + w1h * p_w1h + read * p_read
            ) / 1e6
            context = inp + read + max(total_write, w5 + w1h)
    return context, cost, calls


def is_handoff_command(command):
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(HOOK_DIR, "handoff-only.py")],
            input=command,
            text=True,
            timeout=10,
        )
        return proc.returncode == 0
    except Exception:
        return False


def deny(reason):
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )


def main():
    payload = json.loads(sys.stdin.read())
    agent_type = payload.get("agent_type") or ""
    if not agent_type:
        return 0
    transcript = resolve_transcript(
        payload.get("transcript_path") or "", payload.get("agent_id") or ""
    )
    if not transcript:
        return 0

    context, cost, calls = measure(transcript)
    decision = gate_policy.agent_budget_decision(
        context, cost, calls, agent_type, load_params()
    )

    if decision["decision"] == "remind":
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": decision["message"],
                }
            },
            sys.stdout,
        )
        return 0
    if decision["decision"] != "stop":
        return 0

    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    reason = decision["reason"]
    if tool_name == "Write":
        if str(tool_input.get("file_path") or "").endswith(".md"):
            return 0
        deny(reason + " Write is only allowed to a *.md path.")
        return 0
    if tool_name == "Bash":
        command = str(tool_input.get("command") or "")
        if gate_policy.agent_type_name(agent_type) in COMMIT_TYPES and is_handoff_command(command):
            return 0
        deny(reason + " Bash is denied.")
        return 0
    deny(reason + " " + tool_name + " is denied.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
