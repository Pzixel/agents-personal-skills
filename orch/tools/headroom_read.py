"""Workflow behind the headroom check of hooks/deny-destructive.py: for each
`kubectl exec` in a Bash command, read the target container's memory limit
(`kubectl get pod -o json`) and working set (`kubectl top pod --containers`),
both reads in parallel under one deadline, and return the policy's decision.
Decisions live in gate_policy (kubectl_exec_targets, headroom_adds,
headroom_facts, headroom_decision); this module only runs the two reads."""

import subprocess
import time

import gate_policy


def _kube_prefix(target):
    args = ["kubectl"]
    for flag, key in (("--context", "context"), ("--kubeconfig", "kubeconfig"), ("-n", "namespace")):
        if target.get(key):
            args += [flag, target[key]]
    return args


def read_facts(target, timeout):
    """Facts for one target; an unreadable or late read becomes an error fact."""
    unreadable = gate_policy.headroom_unreadable_pod(target)
    if unreadable:
        return unreadable
    base = _kube_prefix(target)
    procs = [
        subprocess.Popen(base + ["get", "pod", target["pod"], "-o", "json"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True),
        subprocess.Popen(base + ["top", "pod", target["pod"], "--containers", "--no-headers"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True),
    ]
    deadline = time.monotonic() + timeout
    outs = []
    for proc in procs:
        try:
            out, _ = proc.communicate(timeout=max(0.0, deadline - time.monotonic()))
            outs.append(out if proc.returncode == 0 else None)
        except subprocess.TimeoutExpired:
            outs.append(None)
    for proc in procs:
        if proc.poll() is None:
            proc.kill()
    if outs[0] is None:
        return {"error": "kubectl get pod %s failed or timed out" % target["pod"]}
    return gate_policy.headroom_facts(target, outs[0], outs[1])


def headroom_check(command, params, targets):
    """{decision: allow|deny, reason, context, ...} for the command's exec
    targets (from the destructive walk), the most severe; None when there are none."""
    if not targets or gate_policy.headroom_acknowledged(command, params):
        return None
    cfg = gate_policy.headroom_params(params)
    deadline = time.monotonic() + float(cfg["read_timeout_seconds"])
    contexts = []
    for target in targets:
        adds = gate_policy.headroom_adds(target["inner"], params, target.get("stdin"))
        facts = None
        if adds[0] is not None:
            left = deadline - time.monotonic()
            facts = read_facts(target, left) if left > 0 else {"error": "read deadline spent"}
        result = gate_policy.headroom_decision(target, adds, facts, params)
        if result["decision"] == "deny":
            return result
        if result["context"]:
            contexts.append(result["context"])
    return {"decision": "allow", "reason": None, "context": "\n".join(contexts) or None}
