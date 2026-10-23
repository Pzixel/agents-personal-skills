import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import gate_policy  # noqa: E402

NOW = 1_800_000_000.0
RUN = "~/.claude/tools/check-run -- bash tools/sim/run.sh 5000 --all 2>&1 | tail -5"


def long_row(minutes_ago, key="run.sh", minutes=5):
    return {"ts": gate_policy.loop_cost_iso(NOW - minutes_ago * 60),
            "duration_ms": minutes * 60000, "agent_type": "main", "key": key}


def ack_row(minutes_ago, key="run.sh"):
    return {"ts": gate_policy.loop_cost_iso(NOW - minutes_ago * 60), "duration_ms": 0,
            "agent_type": "main", "key": key, "ack": "resume from save", "command": ""}


def decide(rows, command=RUN):
    return gate_policy.loop_cost_decision(rows, command, NOW)["decision"]


class CommandIdentityTest(unittest.TestCase):
    def test_wrapped_forms_of_one_script_share_the_key(self):
        forms = [
            "cd /r/.worktrees/a && RT=x ~/.claude/tools/check-run -- zsh -c 'tools/sim/run.sh 20000 --from 10000 > o.json 2>&1'",
            "export A=1; time nohup tools/sim/run.sh 5000 a b > /tmp/l 2>&1; tail -c 600 /tmp/l",
            "rtk proxy bash tools/sim/run.sh 50000 --all | grep -c save",
        ]
        self.assertEqual({gate_policy.command_identity(c) for c in forms}, {"run.sh"})

    def test_interpreter_takes_the_script_not_the_interpreter(self):
        self.assertNotEqual(
            gate_policy.command_identity("python3 -u a.py"),
            gate_policy.command_identity("python3 -u b.py"),
        )


class LoopCostDecisionTest(unittest.TestCase):
    def test_third_long_run_of_one_key_is_denied(self):
        self.assertEqual(decide([long_row(30), long_row(20)]), "allow")
        self.assertEqual(decide([long_row(30), long_row(20), long_row(10)]), "deny")

    def test_long_minutes_of_one_key_reach_the_budget(self):
        self.assertEqual(decide([long_row(90, minutes=29), long_row(40, minutes=29)]), "deny")

    def test_key_that_never_ran_long_passes_while_another_is_over_budget(self):
        rows = [long_row(m, key="other.py", minutes=20) for m in (100, 70, 40)]
        self.assertEqual(decide(rows), "allow")

    def test_acknowledgement_restarts_the_count_until_three_more_runs(self):
        rows = [long_row(60), long_row(50), long_row(40), ack_row(35), long_row(30), long_row(20)]
        self.assertEqual(decide(rows), "allow")
        self.assertEqual(decide(rows + [long_row(10)]), "deny")

    def test_acknowledgement_on_another_key_does_not_reset(self):
        rows = [long_row(60), long_row(50), long_row(40), ack_row(35, key="other.py")]
        self.assertEqual(decide(rows), "deny")

    def test_rows_outside_the_window_are_not_counted(self):
        rows = [long_row(7 * 60), long_row(6 * 60 + 30), long_row(10)]
        self.assertEqual(decide(rows), "allow")

    def test_marker_line_answers_the_denial(self):
        rows = [long_row(30), long_row(20), long_row(10)]
        decision = gate_policy.loop_cost_decision(
            rows, "# LOOP_COST: resume from the saved frame\n" + RUN, NOW)
        self.assertEqual(decision, {"decision": "override", "reason": "resume from the saved frame", "key": "run.sh"})

    def test_wait_is_journaled_but_never_denied(self):
        wait = "~/.claude/tools/wait-for --every 30 --max 270 -- grep -q exit tmp/run.log"
        row = gate_policy.loop_cost_row(wait, 270000, "", NOW)
        self.assertEqual(row["key"], "")
        self.assertEqual(decide([long_row(30), long_row(20), long_row(10), row], wait), "allow")

    def test_short_command_is_not_journaled(self):
        self.assertIsNone(gate_policy.loop_cost_row(RUN, 119000, "", NOW))


if __name__ == "__main__":
    unittest.main()
