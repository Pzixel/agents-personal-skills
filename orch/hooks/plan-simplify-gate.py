#!/usr/bin/env python3
"""PreToolUse hook for ExitPlanMode: a plan goes to the owner only after /simplify.

The newest plan file in <config dir>/plans must have a sibling
`<plan>.simplify.md` written after the plan's last edit (findings of the
five-angle /simplify pass: reuse, simplification, efficiency, altitude,
premises). Editing the plan afterwards makes the review stale. Any error
exits 0.
"""
import glob
import os
import sys

REASON = (
    "plan-simplify-gate: run /simplify on the plan %s (reuse, simplification, "
    "efficiency, altitude, premises), write the findings to %s, rework the plan "
    "from them, then call ExitPlanMode again."
)


def decision(plan, plan_mtime, review_mtime):
    """Return a refusal message, or None when the review is newer than the plan."""
    if review_mtime is not None and review_mtime >= plan_mtime:
        return None
    return REASON % (plan, review_path(plan))


def review_path(plan):
    return plan[: -len(".md")] + ".simplify.md"


def newest_plan(plans_dir):
    plans = [
        p for p in glob.glob(os.path.join(plans_dir, "*.md")) if not p.endswith(".simplify.md")
    ]
    return max(plans, key=os.path.getmtime) if plans else None


def main():
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    plan = newest_plan(os.path.join(config_dir, "plans"))
    if plan is None:
        return 0
    review = review_path(plan)
    review_mtime = os.path.getmtime(review) if os.path.exists(review) else None
    reason = decision(plan, os.path.getmtime(plan), review_mtime)
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
