#!/usr/bin/env python3
"""PreToolUse hook for ExitPlanMode: a plan goes to the owner only after /simplify.

Mechanical: gathers facts from the session transcript and hands them to
gate_policy.plan_simplify_decision. The plan is the last `plans/<name>.md` the
session wrote (Write/Edit/MultiEdit file_path; `-agent-*.md` drafts and
`.simplify.md` findings excluded). Lens agents are the session's subagent
transcripts `<dir>/<session_id>/subagents/agent-<id>.jsonl` whose meta file
says spawnDepth 1, written at or after the plan's creation, whose first user
message names the plan file. No plan in the transcript, or any error, exits 0.
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
_DRAFT = re.compile(r"-agent-[^/]*\.md$")


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


def lens_agents(subagents_dir, plan, plan_created):
    name = os.path.basename(plan)
    count = 0
    for path in glob.glob(os.path.join(subagents_dir, "agent-*.jsonl")):
        meta_path = path[: -len(".jsonl")] + ".meta.json"
        if not os.path.exists(meta_path) or os.path.getmtime(path) < plan_created:
            continue
        with open(meta_path) as fh:
            if json.load(fh).get("spawnDepth") != 1:
                continue
        if name in first_user_text(path):
            count += 1
    return count


def main():
    payload = json.load(sys.stdin)
    transcript, session_id = payload.get("transcript_path"), payload.get("session_id")
    if not transcript or not session_id:
        return 0
    plan = last_plan_written(transcript)
    if plan is None or not os.path.exists(plan):
        return 0
    st = os.stat(plan)
    created = getattr(st, "st_birthtime", st.st_mtime)
    review = gate_policy.plan_review_path(plan)
    review_mtime = os.path.getmtime(review) if os.path.exists(review) else None
    with open(plan) as fh:
        words = len(fh.read().split())
    subagents = os.path.join(os.path.dirname(transcript), session_id, "subagents")
    reason = gate_policy.plan_simplify_decision(
        plan, words, created, review_mtime, lens_agents(subagents, plan, created),
        gate_policy.load_params(),
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
