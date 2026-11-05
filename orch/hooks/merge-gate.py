#!/usr/bin/env python3
"""PreToolUse hook for Bash: a change lands on a shared branch only with the
real-path check (service start plus one unit through natively, against the shared local store container or a production read route, never a container per task)
named in the command.

`gh pr merge` and `git push` to master/main are refused unless the command
carries `CHECK_RAN: <what ran on the real path, result>`, `PROD_POSITION:
<blast-radius dimension, production's number, source>`, `WAY_BACK: <undo,
minutes>` and `WORSE_FOR: <who gets worse or can break for every consumer,
the new bottleneck; or none, with the reason>`, and `ALWAYS_ON:` when the pushed range changes an always-on
rules file. The tokens are the agent's own statements; the gate only makes
them unavoidable at the moment of merging. A delegate's landing at command
position is refused whatever it carries (CASE-62). A landing (`is_landing`)
on a branch whose tip workflow runs are red is refused unless it carries
`# CI_FIX: <sha7> <what the push repairs>`; one `gh run list` in the target
repository, 4 s, fail open. Decision: gate_policy.merge_gate_decision. Any error exits 0.
"""
import json
import os
import subprocess
import sys

_PLUGIN_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
TOOLS_DIR = (
    _PLUGIN_TOOLS if os.path.isdir(_PLUGIN_TOOLS) else os.path.expanduser("~/.claude/tools")
)
sys.path.insert(0, TOOLS_DIR)

import gate_policy  # noqa: E402


def _pushed_paths(command, cwd):
    """Paths the pushed range changes, or None when unknown (fail open)."""
    rng = gate_policy.push_range(command, cwd)
    if rng is None:
        return None
    try:
        out = subprocess.run(
            ["git", "-C", rng["dir"], "diff", "--name-only",
             "%s/%s...%s" % (rng["remote"], rng["target"], rng["src"])],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.split() if out.returncode == 0 else None


def _read(args, cwd, timeout):
    """stdout of ``args`` run in ``cwd``, or the failure cause as an exception."""
    out = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError("%s exit %d: %s" % (args[0], out.returncode, out.stderr.strip()[:120]))
    return out.stdout


def _ci_state(command, cwd):
    """gate_policy.ci_tip_state of the landing's target branch, read in the
    landing's repository directory; any read failure is an unknown state
    with its cause (fail open)."""
    target = gate_policy.landing_target(command, cwd)
    directory, branch = target["dir"], target["branch"]
    if not directory:
        return {"state": "unknown", "cause": "repository directory not literal"}
    try:
        if not branch and target["pr"]:
            branch = json.loads(_read(["gh", "pr", "view", target["pr"], "--json", "baseRefName"],
                                      directory, 4))["baseRefName"]
        if not branch:
            branch = _read(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
                           directory, 3).strip().split("/", 1)[-1]
        runs = json.loads(_read(["gh", "run", "list", "--branch", branch, "--event", "push", "--limit", "20",
                                 "--json", "status,conclusion,databaseId,headSha,event,workflowName"],
                                directory, 4))
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        return {"state": "unknown", "cause": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    return gate_policy.ci_tip_state(runs if isinstance(runs, list) else None)


def main():
    payload = json.load(sys.stdin)
    command = str((payload.get("tool_input") or {}).get("command") or "")
    delegate = gate_policy.is_delegate(payload)
    # The delegate verdict needs no paths; the git diff runs only when it is not that.
    decision = gate_policy.merge_gate_decision(command, None, delegate)
    if decision.get("kind") != "delegate":
        ci = _ci_state(command, payload.get("cwd")) if gate_policy.is_landing(command) else None
        decision = gate_policy.merge_gate_decision(
            command, _pushed_paths(command, payload.get("cwd")), delegate, ci)
    if decision["decision"] == "deny":
        print(decision["reason"], file=sys.stderr)
        return 2
    return 0


try:
    sys.exit(main())
except SystemExit:
    raise
except BaseException:
    sys.exit(0)
