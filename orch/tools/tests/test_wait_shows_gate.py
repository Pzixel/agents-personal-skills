import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import gate_policy  # noqa: E402

# The CASE-68 witness: created 15:38 local, one-shot at 07:43 next day.
NOW = datetime.datetime(2026, 11, 5, 15, 38, tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
FACT = "WAIT_SHOWS: the Telegram delivery of the first digest on the new image\n"


def cron(prompt="check the digest numbers", cron_expr="43 7 6 11 *", recurring=False):
    return gate_policy.wait_shows_decision(
        "CronCreate", {"cron": cron_expr, "prompt": prompt, "recurring": recurring}, NOW)


def monitor(timeout_ms, command="wait-for -- true", description="deploy"):
    return gate_policy.wait_shows_decision(
        "Monitor", {"command": command, "description": description, "timeout_ms": timeout_ms}, NOW)


def bash(command):
    return gate_policy.wait_shows_decision("Bash", {"command": command}, NOW)


class CronTest(unittest.TestCase):
    def test_witness_one_shot_without_the_line_is_refused_with_its_horizon(self):
        result = cron()
        self.assertEqual(result["decision"], "deny")
        self.assertIn("16.1 h ahead", result["reason"])

    def test_a_named_fact_allows(self):
        self.assertEqual(cron(FACT + "check")["decision"], "allow")

    def test_an_empty_value_counts_as_missing(self):
        for prompt in ("WAIT_SHOWS:\ncheck the digest numbers", "WAIT_SHOWS: \ncheck"):
            with self.subTest(prompt=prompt):
                result = cron(prompt)
                self.assertEqual(result["decision"], "deny")
                self.assertIn("16.1 h ahead", result["reason"])

    def test_exactly_an_hour_ahead_is_gated(self):
        self.assertEqual(cron(cron_expr="38 16 * * *")["decision"], "deny")
        self.assertEqual(cron(cron_expr="37 16 * * *")["decision"], "allow")

    def test_null_answers_get_the_nothing_reason(self):
        for value in ("nothing", "None", "-", "n/a"):
            with self.subTest(value=value):
                result = cron("WAIT_SHOWS: %s\ncheck" % value)
                self.assertEqual(result["decision"], "deny")
                self.assertIn("WAIT_SHOWS is nothing", result["reason"])

    def test_a_time_already_past_today_rolls_to_tomorrow(self):
        self.assertEqual(cron(cron_expr="30 15 * * *")["decision"], "deny")

    def test_under_an_hour_ahead_is_allowed(self):
        self.assertEqual(cron(cron_expr="8 16 * * *")["decision"], "allow")

    def test_recurring_and_malformed_crons_are_allowed(self):
        self.assertEqual(cron(recurring=True)["decision"], "allow")
        self.assertEqual(cron(cron_expr="*/5 * * *")["decision"], "allow")
        self.assertEqual(cron(cron_expr="0 9 31 2 *")["decision"], "allow")


class MonitorTest(unittest.TestCase):
    def test_horizon_threshold(self):
        self.assertEqual(monitor(1500000)["decision"], "allow")
        self.assertEqual(monitor(5400000)["decision"], "deny")
        self.assertEqual(monitor("5400000")["decision"], "deny")
        self.assertEqual(monitor(3600000)["decision"], "deny")
        self.assertEqual(monitor(3540000)["decision"], "allow")

    def test_line_in_command_or_description_allows(self):
        self.assertEqual(monitor(5400000, command="# " + FACT + "wait-for -- true")["decision"], "allow")
        self.assertEqual(monitor(5400000, description=FACT)["decision"], "allow")

    def test_absent_timeout_fails_open(self):
        self.assertEqual(monitor(None)["decision"], "allow")


class BashTest(unittest.TestCase):
    def test_long_pure_wait_needs_the_line(self):
        self.assertEqual(bash("wait-for --max 7200 -- test -f done")["decision"], "deny")
        self.assertEqual(bash("# " + FACT + "wait-for --max 7200 -- test -f done")["decision"], "allow")

    def test_short_wait_and_non_wait_are_allowed(self):
        self.assertEqual(bash("wait-for --max 600 -- test -f done")["decision"], "allow")
        self.assertEqual(bash("pytest -q")["decision"], "allow")


if __name__ == "__main__":
    unittest.main()
