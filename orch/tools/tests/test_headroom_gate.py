import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import gate_policy  # noqa: E402

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hooks", "deny-destructive.py")
MI = 1024 ** 2
INCIDENT = ("kubectl --context prod -n ns-a exec broker-0 -c kafka -- env "
            "KAFKA_HEAP_OPTS=-Xmx256m /opt/kafka/bin/kafka-get-offsets.sh --bootstrap-server "
            "localhost:9092 --topic inbound.received.dlq.v1")
TARGET = {"pod": "broker-0", "container": "kafka"}


def facts(limit_mi, use_mi):
    return {"limit": limit_mi * MI, "use": use_mi * MI, "container": "kafka", "error": None}


class ExecTargetTest(unittest.TestCase):
    def test_every_route_to_an_exec_yields_pod_and_container(self):
        forms = [
            "kubectl -n ns-a exec -c kafka broker-0 -- ls",
            "rtk proxy kubectl --context=prod exec broker-0 --container=kafka -- ls",
            'k() { rtk proxy kubectl --context prod --request-timeout=20s "$@"; }; '
            "k -n ns-a exec broker-0 -c kafka -- ls",
            'bash -c "kubectl exec pod/broker-0 -c kafka -- ls"',
            "timeout 30 env A=1 kubectl exec -it broker-0 -c kafka -- ls",
            "cat <<EOF | bash\nkubectl exec broker-0 -c kafka -- ls\nEOF",
        ]
        for command in forms:
            with self.subTest(command=command):
                targets = gate_policy.kubectl_exec_targets(command)
                self.assertEqual([(t["pod"], t["container"], t["inner"]) for t in targets],
                                 [("broker-0", "kafka", ["ls"])])

    def test_wrapper_function_carries_its_context_to_the_reads(self):
        command = 'k() { kubectl --context k3s-staging "$@"; }; k exec p -- ls'
        self.assertEqual(gate_policy.kubectl_exec_targets(command)[0]["context"], "k3s-staging")

    def test_no_exec_no_target(self):
        for command in ["kubectl -n ns-a logs broker-0 -c kafka", "echo kubectl exec", "ls"]:
            with self.subTest(command=command):
                self.assertEqual(gate_policy.kubectl_exec_targets(command), [])


class AddsTest(unittest.TestCase):
    def test_jvm_with_heap_cap_adds_heap_plus_overhead(self):
        target = gate_policy.kubectl_exec_targets(INCIDENT)[0]
        self.assertEqual(gate_policy.headroom_adds(target["inner"])[0], 256 * MI + 192 * MI)

    def test_jvm_without_heap_cap_is_unknown(self):
        # cluster-b 2026-10-18: console consumers inherited the broker's heap env.
        inner = ["/opt/kafka/bin/kafka-console-consumer.sh", "--bootstrap-server", "localhost:9092", "--topic", "t"]
        self.assertIsNone(gate_policy.headroom_adds(inner)[0])

    def test_jvm_is_found_by_the_programs_run_not_by_arguments(self):
        cases = [
            (["sh", "-c", "for p in /proc/*; do grep java $p/cmdline; done"], False),
            (["ls", "/opt/kafka/bin/"], False),
            (["grep", "-r", "KAFKA_HEAP_OPTS=-Xmx1g", "/etc"], False),
            (["sh", "-c", "ls /tmp; /opt/kafka/bin/kafka-topics.sh --list"], True),
            (["bash", "-lc", "kafka-topics.sh --list"], True),
            (["bin/kafka-consumer-groups.sh", "--list"], True),
            (["kafka-get-offsets.sh", "--topic", "t"], True),
            (["sh", "-c", "B=/opt/kafka/bin; $B/kafka-get-offsets.sh --topic t"], True),
            (["/opt/java/openjdk/bin/java", "-version"], True),
        ]
        for inner, jvm in cases:
            with self.subTest(inner=inner):
                self.assertEqual(gate_policy.headroom_adds(inner)[0] != 32 * MI, jvm)

    def test_heredoc_fed_to_the_exec_shell_is_read(self):
        command = "kubectl exec broker-0 -i -- sh <<EOF\n/opt/kafka/bin/kafka-topics.sh --list\nEOF"
        target = gate_policy.kubectl_exec_targets(command)[0]
        self.assertIsNone(gate_policy.headroom_adds(target["inner"], None, target["stdin"])[0])

    def test_heap_exported_earlier_in_the_script_caps_the_jvm(self):
        scripts = [
            'export KAFKA_HEAP_OPTS="-Xmx256m"; /opt/kafka/bin/kafka-topics.sh --list',
            "X=-Xmx256m; export X; /opt/kafka/bin/kafka-topics.sh --list",
            "B=/opt/kafka/bin; export X='-Xmx256m'; for p in 0 1; do $B/kafka-get-offsets.sh; done",
        ]
        for script in scripts:
            with self.subTest(script=script):
                self.assertEqual(gate_policy.headroom_adds(["sh", "-c", script])[0], 256 * MI + 192 * MI)

    def test_heap_env_of_an_enclosing_shell_caps_the_jvm(self):
        inner = ["env", "KAFKA_HEAP_OPTS=-Xmx256m", "bash", "-c", "for p in 0 1; do /opt/kafka/bin/kafka-get-offsets.sh; done"]
        self.assertEqual(gate_policy.headroom_adds(inner)[0], 256 * MI + 192 * MI)

    def test_plain_process_adds_the_floor(self):
        self.assertEqual(gate_policy.headroom_adds(["cat", "/etc/hostname"])[0], 32 * MI)


class DecisionTest(unittest.TestCase):
    def test_incident_numbers_deny(self):
        result = gate_policy.headroom_decision(TARGET, (448 * MI, "a JVM"), facts(1024, 904))
        self.assertEqual(result["decision"], "deny")
        self.assertIn("limit 1024Mi, working set 904Mi, free 120Mi", result["reason"])

    def test_mi_old_jvm_without_heap_denies_despite_free_memory(self):
        result = gate_policy.headroom_decision(TARGET, (None, "a JVM"), facts(8192, 3686))
        self.assertEqual(result["decision"], "deny")

    def test_near_miss_allows_with_numbers(self):
        result = gate_policy.headroom_decision(TARGET, (448 * MI, "a JVM"), facts(1024, 400))
        self.assertEqual((result["decision"], "free 624Mi" in result["context"]), ("allow", True))

    def test_plain_allow_is_silent(self):
        result = gate_policy.headroom_decision(TARGET, (32 * MI, "a process"), facts(512, 300))
        self.assertEqual((result["decision"], result["context"]), ("allow", None))

    def test_unknown_facts_allow_with_the_cause(self):
        cases = [({"error": "kubectl get pod p failed or timed out"}, "headroom unknown (kubectl get pod"),
                 ({"limit": None, "use": None, "container": "kafka", "error": None}, "no memory limit on")]
        for fact, text in cases:
            with self.subTest(text=text):
                result = gate_policy.headroom_decision(TARGET, (32 * MI, "a process"), fact)
                self.assertEqual((result["decision"], text in result["context"]), ("allow", True))



class FactsTest(unittest.TestCase):
    POD = json.dumps({"metadata": {"annotations": {"kubectl.kubernetes.io/default-container": "kafka"}},
                      "spec": {"containers": [{"name": "sidecar", "resources": {"limits": {"memory": "64Mi"}}},
                                              {"name": "kafka", "resources": {"limits": {"memory": "1Gi"}}}]}})
    TOP = "broker-0   sidecar   1m   10Mi\nbroker-0   kafka   50m   904Mi\n"

    def test_default_container_annotation_picks_the_container(self):
        got = gate_policy.headroom_facts({"pod": "broker-0", "container": None}, self.POD, self.TOP)
        self.assertEqual((got["container"], got["limit"], got["use"]), ("kafka", 1024 * MI, 904 * MI))

    def test_deployment_target_is_unknown(self):
        got = gate_policy.headroom_facts({"pod": "deploy/x", "container": None}, None, None)
        self.assertIn("not resolved", got["error"])

    def test_missing_metrics_is_unknown(self):
        got = gate_policy.headroom_facts({"pod": "broker-0", "container": "kafka"}, self.POD, None)
        self.assertIn("metrics-server", got["error"])


class HookFailsOpenTest(unittest.TestCase):
    def run_hook(self, kubectl_script):
        bindir = tempfile.TemporaryDirectory()
        self.addCleanup(bindir.cleanup)
        if kubectl_script:
            path = os.path.join(bindir.name, "kubectl")
            with open(path, "w") as fh:
                fh.write(kubectl_script)
            os.chmod(path, 0o755)
        payload = {"session_id": "x", "tool_name": "Bash", "tool_input": {"command": INCIDENT}}
        start = time.monotonic()
        proc = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                              text=True, env=dict(os.environ, PATH=bindir.name), timeout=20)
        return proc, time.monotonic() - start

    def test_no_kubectl_allows_with_unknown_headroom(self):
        proc, _ = self.run_hook(None)
        out = json.loads(proc.stdout)["hookSpecificOutput"]
        self.assertEqual((proc.returncode, "permissionDecision" in out), (0, False))
        self.assertIn("headroom unknown", out["additionalContext"])

    def test_hanging_kubectl_answers_within_the_hook_timeout(self):
        proc, wall = self.run_hook("#!/bin/sh\n/bin/sleep 10\n")
        out = json.loads(proc.stdout)["hookSpecificOutput"]
        self.assertIn("failed or timed out", out["additionalContext"])
        self.assertLess(wall, 5.0)


if __name__ == "__main__":
    unittest.main()
