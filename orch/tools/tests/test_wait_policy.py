import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))

import gate_policy  # noqa: E402


def poll(cmd, background=False, agent_type="", params=None):
    return gate_policy.poll_decision(cmd, background, agent_type, params)["decision"]


class PacingSleepTest(unittest.TestCase):
    def test_short_sleep_pacing_a_request_loop_is_allowed(self):
        self.assertEqual(
            poll('for p in 1 2 3; do curl -s "https://api/x?page=$p"; sleep 0.2; done'),
            "allow",
        )

    def test_short_sleep_in_while_cursor_loop_is_allowed(self):
        self.assertEqual(
            poll("while read c; do ./fetch.sh $c; sleep 0.2; done < cursors"), "allow"
        )

    def test_long_sleep_in_loop_is_denied(self):
        self.assertEqual(poll("until gh run view 1; do sleep 30; done"), "deny")

    def test_bare_long_sleep_is_denied(self):
        self.assertEqual(poll("sleep 30"), "deny")

    def test_sleep_without_argument_is_denied(self):
        self.assertEqual(poll("sleep"), "deny")

    def test_threshold_comes_from_params(self):
        self.assertEqual(poll("sleep 3", params={"poll_sleep_min_seconds": 2}), "deny")

    def test_quoted_sleep_is_not_polling(self):
        self.assertEqual(poll('grep -r "sleep 30" .'), "allow")


class SubagentBackgroundTest(unittest.TestCase):
    def test_subagent_background_call_is_denied(self):
        self.assertEqual(poll("./long.sh", background=True, agent_type="prod-probe"), "deny")

    def test_main_session_background_call_is_allowed(self):
        self.assertEqual(poll("./long.sh", background=True, agent_type=""), "allow")


class SubagentWaitForTimeoutTest(unittest.TestCase):
    CMD = "~/.claude/tools/wait-for --every 30 --max {} -- gh run view 1"

    def test_subagent_wait_for_within_its_call_is_allowed(self):
        self.assertEqual(
            gate_policy.poll_decision(self.CMD.format(570), False, "builder", None, 600000)["decision"],
            "allow",
        )

    def test_subagent_wait_for_on_default_timeout_is_denied(self):
        self.assertEqual(
            gate_policy.poll_decision(self.CMD.format(570), False, "builder", None, None)["decision"],
            "deny",
        )

    def test_subagent_wait_for_longer_than_a_call_is_denied(self):
        self.assertEqual(
            gate_policy.poll_decision(self.CMD.format(1800), False, "builder", None, 600000)["decision"],
            "deny",
        )

    def test_main_session_wait_for_is_unchanged(self):
        self.assertEqual(
            gate_policy.poll_decision(self.CMD.format(1800), False, "", None, None)["decision"],
            "allow",
        )


class SleepingStopDecisionTest(unittest.TestCase):
    def decide(self, text, ids=(), active=False, seen=False):
        return gate_policy.sleeping_stop_decision(text, list(ids), active, seen)["decision"]

    def test_waiting_reply_is_returned(self):
        self.assertEqual(
            self.decide("Still running with no output yet. I'll end this turn now and wait "
                        "for the background task notification."),
            "block",
        )

    def test_unresolved_background_task_is_returned(self):
        self.assertEqual(self.decide("Report written to out.md.", ids=["bo56qwxwe"]), "block")

    def test_report_quoting_the_phrase_in_backticks_is_let_through(self):
        self.assertEqual(
            self.decide("Done. The phrase `still running` now matches only unquoted text."),
            "allow",
        )

    def test_report_ending_with_unquoted_wait_is_returned(self):
        self.assertEqual(
            self.decide("Fetched 40 of 100 pages. I'll end this turn now and wait."), "block"
        )

    def test_finished_reply_is_let_through(self):
        self.assertEqual(self.decide("Report written to out.md. 100 pages fetched."), "allow")

    def test_second_stop_after_block_is_let_through(self):
        self.assertEqual(self.decide("Still running.", ids=["b1"], seen=True), "allow")

    def test_stop_hook_active_without_own_marker_is_returned(self):
        self.assertEqual(self.decide("Still running.", active=True), "block")

    def test_own_marker_seen_is_let_through(self):
        self.assertEqual(self.decide("Still running.", active=True, seen=True), "allow")


if __name__ == "__main__":
    unittest.main()
