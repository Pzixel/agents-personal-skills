#!/usr/bin/env python3
"""PreToolUse hook: a plan or a builder's design moves on only after /simplify.

Three entry points, one policy (gate_policy.plan_simplify_decision):
- ExitPlanMode: the plan goes to the owner.
- SendMessage to a builder starting `design reviewed` and containing
  `proceed to code` (the template go-ahead): the builder starts code.
- Agent spawning a builder whose prompt carries `handoff:`.
Which call applies and which DESIGN.md it moves is gate_policy.design_gate_target;
no `workspace:` line in the builder prompt, or no DESIGN.md on disk, passes
(fail-open, like the plan gate). Freshness of `<notes>/DESIGN.simplify.md`:
for SendMessage its mtime must be at or after the last content change of
DESIGN.md, max(st_birthtime, st_mtime) (the Write tool replaces the inode, an
in-place edit keeps it); for a handoff spawn it only has to exist: a handoff
at the design stage has none and is refused, one after `proceed to code` has
one and passes although the builder edited DESIGN.md while applying the
findings. A design lens agent is a depth-1 agent whose first user message
contains the full DESIGN.md path and the literal `/simplify`.

ExitPlanMode: the plan is the last `plans/<name>.md` the
session wrote (Write/Edit/MultiEdit file_path; `-agent-*.md` drafts and
`.simplify.md` findings excluded); the findings must postdate its st_birthtime,
since the plan is reworked from them. Lens agents are the session's subagent
transcripts `<dir>/<session_id>/subagents/agent-<id>.jsonl` whose meta file
says spawnDepth 1 and whose first user message names the plan file, at any
time: Write and Edit replace the plan's inode, so no file time marks its first
creation. No plan in the transcript, or any error, exits 0.
"""
import glob
import json
import os
import re
import sys

TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, TOOLS_DIR)

import gate_policy  # noqa: E402

_WRITE_TOOLS = ("Write", "Edit", "MultiEdit")
_DRAFT = re.compile(r"-agent-[0-9a-f]+\.md$")


def is_plan_path(path):
    return (
        "/plans/" in path
        and path.endswith(".md")
        and not path.endswith(".simplify.md")
        and not _DRAFT.search(path)
    )


def last_plan_written(transcript):
    plan = None
    with open(transcript) as fh:
        for line in fh:
            if '"tool_use"' not in line or "/plans/" not in line:
                continue
            content = (json.loads(line).get("message") or {}).get("content")
            for part in content if isinstance(content, list) else ():
                if part.get("type") == "tool_use" and part.get("name") in _WRITE_TOOLS:
                    path = (part.get("input") or {}).get("file_path") or ""
                    if is_plan_path(path):
                        plan = path
    return plan


def first_user_text(transcript):
    with open(transcript) as fh:
        for line in fh:
            entry = json.loads(line)
            if entry.get("type") != "user":
                continue
            content = (entry.get("message") or {}).get("content")
            if isinstance(content, str):
                return content
            return " ".join(p.get("text", "") for p in content or () if isinstance(p, dict))
    return ""


def lens_agents(subagents_dir, needles):
    count = 0
    for path in glob.glob(os.path.join(subagents_dir, "agent-*.jsonl")):
        meta_path = path[: -len(".jsonl")] + ".meta.json"
        if not os.path.exists(meta_path):
            continue
        with open(meta_path) as fh:
            if json.load(fh).get("spawnDepth") != 1:
                continue
        text = first_user_text(path)
        if all(n in text for n in needles):
            count += 1
    return count


def recipient(subagents, agent_id):
    """(agentType, first user message) of a session subagent, or (None, "")."""
    base = os.path.join(subagents, "agent-%s" % agent_id)
    if not os.path.exists(base + ".meta.json"):
        return None, ""
    with open(base + ".meta.json") as fh:
        agent_type = json.load(fh).get("agentType")
    return agent_type, first_user_text(base + ".jsonl")


def main():
    payload = json.load(sys.stdin)
    transcript, session_id = payload.get("transcript_path"), payload.get("session_id")
    if not transcript or not session_id:
        return 0
    subagents = os.path.join(os.path.dirname(transcript), session_id, "subagents")
    tool, tool_input = payload.get("tool_name"), payload.get("tool_input") or {}
    if tool == "ExitPlanMode":
        plan, kind = last_plan_written(transcript), "plan"
    else:
        facts = recipient(subagents, tool_input.get("to")) if tool == "SendMessage" else (None, "")
        plan, kind = gate_policy.design_gate_target(tool, tool_input, *facts) or (None, None)
    if plan is None or not os.path.exists(plan):
        return 0
    needles = (os.path.basename(plan),) if kind == "plan" else (plan, "/simplify")
    st = os.stat(plan)
    birth = getattr(st, "st_birthtime", st.st_mtime)
    changed = birth if kind == "plan" else max(birth, st.st_mtime)
    review = gate_policy.plan_review_path(plan)
    review_mtime = os.path.getmtime(review) if os.path.exists(review) else None
    with open(plan) as fh:
        words = len(fh.read().split())
    reason = gate_policy.plan_simplify_decision(
        plan, words, changed, review_mtime, lens_agents(subagents, needles),
        gate_policy.load_params(), kind,
    )
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


try:
    sys.exit(main())
except SystemExit:
    raise
except BaseException:
    sys.exit(0)
