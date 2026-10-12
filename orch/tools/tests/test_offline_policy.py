"""Cases for gate_policy.offline_decision.

The contract under test is the owner's rule for an offline experiment session:
a command that can write beyond this machine, or touch a deployment control
plane at all, is refused; everything local, and every remote read, still runs.
One representative per class; the parameters come from the defaults, so a case
states the rule and not a repository's tool list.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

import gate_policy  # noqa: E402


def decide(command):
    return gate_policy.offline_decision(command)["decision"]


class OfflineDecisionTest(unittest.TestCase):
    def test_control_plane_program_is_refused_whatever_it_does(self):
        self.assertEqual(decide("kubectl get pods -n dataplane"), "deny")

    def test_reason_names_the_offline_experiment(self):
        self.assertIn(
            "offline experiment session",
            gate_policy.offline_decision("helm list")["reason"],
        )

    def test_local_git_work_runs_but_publishing_does_not(self):
        self.assertEqual(decide("git commit -m 'fix'"), "allow")
        self.assertEqual(decide("git push origin main"), "deny")

    def test_wrapper_prefix_does_not_hide_the_program(self):
        self.assertEqual(decide("rtk git push"), "deny")

    def test_command_after_cd_is_examined(self):
        self.assertEqual(decide("cd /tmp/x && kubectl get pods"), "deny")

    def test_github_reads_pass_and_writes_do_not(self):
        self.assertEqual(decide("gh pr view 931"), "allow")
        self.assertEqual(decide("gh pr merge 931 --squash"), "deny")

    def test_github_api_is_a_read_until_it_carries_a_write_flag(self):
        self.assertEqual(decide("gh api repos/acme/backend/pulls"), "allow")
        self.assertEqual(decide("gh api -X POST repos/acme/backend/issues"), "deny")

    def test_database_client_is_refused_only_for_a_remote_host(self):
        self.assertEqual(decide("psql -h db.prod.example.internal -c 'select 1'"), "deny")
        self.assertEqual(decide("psql -h localhost -c 'select 1'"), "allow")

    def test_http_write_to_a_remote_host_is_refused_and_a_read_is_not(self):
        self.assertEqual(decide("curl https://api.example.com/v1/items"), "allow")
        self.assertEqual(
            decide("curl -X POST https://api.example.com/v1/items -d '{}'"), "deny"
        )

    def test_http_write_to_this_machine_still_runs(self):
        self.assertEqual(decide("curl -X POST http://localhost:7777/sync -d '{}'"), "allow")

    def test_flag_named_like_a_remote_shell_is_not_a_remote_shell(self):
        self.assertEqual(decide("docker build --ssh default -t app ."), "allow")


if __name__ == "__main__":
    unittest.main()
