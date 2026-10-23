import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import gate_policy  # noqa: E402

NOW = 1_800_000_000.0
RUN = "~/.claude/tools/check-run -- bash tools/sim/run.sh 5000 --all 2>&1 | tail -5"
PUNCTUATED = "# LOOP_COST: resume from save (the affected target only); skip the rest && | done\n"


def long_row(minutes_ago, key="run.sh", minutes=5, agent_type="main"):
    return {"ts": gate_policy.loop_cost_iso(NOW - minutes_ago * 60),
            "duration_ms": minutes * 60000, "agent_type": agent_type, "key": key}


def ack_row(minutes_ago, key="run.sh", reason="resume from save"):
    return {"ts": gate_policy.loop_cost_iso(NOW - minutes_ago * 60), "duration_ms": 0,
            "agent_type": "main", "key": key, "ack": reason, "command": ""}


def decide(rows, command=RUN):
    return gate_policy.loop_cost_decision(rows, command, NOW)["decision"]


class CommandIdentityTest(unittest.TestCase):
    def test_wrapped_forms_of_one_script_share_the_key(self):
        forms = [
            "cd /r/.worktrees/a && RT=x ~/.claude/tools/check-run -- zsh -c 'tools/sim/run.sh 20000 --from 10000 > o.json 2>&1'",
            "export A=1; time nohup tools/sim/run.sh 5000 a b > /tmp/l 2>&1; tail -c 600 /tmp/l",
            "rtk proxy bash tools/sim/run.sh 50000 --all | grep -c save",
            "timeout 600 bash tools/sim/run.sh 5000",
            "nice -n 5 bash tools/sim/run.sh",
            "sudo -u bob caffeinate -i bash tools/sim/run.sh",
            "env A=1 timeout -s KILL 10m bash tools/sim/run.sh",
            "bash tools/sim/run.sh $(cat n) --all",
            "bash tools/sim/run.sh 5000  # rerun; after the fix (x)",
        ]
        self.assertEqual({c: gate_policy.command_identity(c) for c in forms},
                         {c: "run.sh" for c in forms})

    def test_runner_wrappers_do_not_become_the_key(self):
        cases = {
            "timeout 600 python3 bench.py": "bench.py",
            "cat files | xargs -n 1 python3 a.py": "a.py",
            "for i in 1 2 3; do python3 a.py $i; done": "a.py",
        }
        self.assertEqual({c: gate_policy.command_identity(c) for c in cases}, cases)

    def test_interpreter_takes_the_script_not_the_interpreter(self):
        self.assertEqual(gate_policy.command_identity("python3 -u a.py"), "a.py")

    def test_punctuated_ack_reason_keeps_the_command_key(self):
        reasons = [PUNCTUATED, "# LOOP_COST: can't resume, no saved state\n"]
        self.assertEqual([gate_policy.command_identity(r + RUN) for r in reasons], ["run.sh", "run.sh"])


class CdTargetTest(unittest.TestCase):
    def test_leading_cd_target_selects_the_directory(self):
        cases = {
            'cd "/a b/c" && bash run.sh': "/a b/c",
            "cd ~/r; bash run.sh": "~/r",
            "cd ../y\nbash run.sh": "../y",
            "cd /x || exit": None,
            "bash run.sh": None,
        }
        self.assertEqual({c: gate_policy.loop_cost_cd_target(c) for c in cases}, cases)


class LoopCostDecisionTest(unittest.TestCase):
    def test_fourth_launch_after_three_long_runs_of_one_key_is_denied(self):
        self.assertEqual(decide([long_row(30), long_row(20)]), "allow")
        self.assertEqual(decide([long_row(30), long_row(20), long_row(10)]), "deny")

    def test_pooled_minutes_deny_a_key_that_ran_long_twice(self):
        rows = [long_row(100, key="other.py", minutes=25), long_row(20), long_row(10)]
        self.assertEqual(decide(rows), "deny")

    def test_pooled_minutes_include_waits(self):
        rows = [long_row(100, key="", minutes=25), long_row(20), long_row(10)]
        self.assertEqual(decide(rows), "deny")

    def test_pooled_minutes_pass_a_key_that_ran_long_once(self):
        rows = [long_row(m, key="other.py", minutes=20) for m in (100, 70, 40)] + [long_row(10)]
        self.assertEqual(decide(rows), "allow")

    def test_ack_of_any_key_restarts_the_pooled_minutes(self):
        rows = [long_row(100, key="other.py", minutes=25), ack_row(60, key="other.py"),
                long_row(20), long_row(10)]
        self.assertEqual(decide(rows), "allow")

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

    def test_punctuated_ack_lets_the_next_launch_through(self):
        rows = [long_row(30), long_row(20), long_row(10)]
        decision = gate_policy.loop_cost_decision(rows, PUNCTUATED + RUN, NOW)
        acked = rows + [dict(ack_row(0, key=decision["key"]), ack=decision["reason"])]
        self.assertEqual((decision["key"], decide(acked)), ("run.sh", "allow"))

    def test_ack_reason_is_bounded(self):
        decision = gate_policy.loop_cost_decision([], "# LOOP_COST: " + "x" * 300 + "\n" + RUN, NOW)
        self.assertEqual(decision["reason"], "x" * 200)

    def test_wait_is_journaled_but_never_denied(self):
        wait = "~/.claude/tools/wait-for --every 30 --max 270 -- grep -q exit tmp/run.log"
        row = gate_policy.loop_cost_row(wait, 270000, "", NOW)
        self.assertEqual(row["key"], "")
        self.assertEqual(decide([long_row(30), long_row(20), long_row(10), row], wait), "allow")

    def test_short_command_is_not_journaled(self):
        self.assertIsNone(gate_policy.loop_cost_row(RUN, 119000, "", NOW))


class DenyTextTest(unittest.TestCase):
    def test_deny_text_folds_top_five_keys_and_last_five_acks(self):
        others = [long_row(200 - i, key="k%d.py" % i, minutes=10 - i) for i in range(6)]
        acks = [ack_row(150 - i, key="k%d.py" % i, reason="ack %d" % i) for i in range(6)]
        rows = others + acks + [long_row(30, agent_type="builder"), long_row(20), long_row(10)]
        text = gate_policy.loop_cost_decision(rows, RUN, NOW)["reason"]
        folded = [line for line in text.splitlines() if " runs, avg " in line]
        self.assertEqual(folded, [
            "  run.sh: 3 runs, avg 5 min, total 15 min, run by builder, main",
            "  k0.py: 1 runs, avg 10 min, total 10 min, run by main",
            "  k1.py: 1 runs, avg 9 min, total 9 min, run by main",
            "  k2.py: 1 runs, avg 8 min, total 8 min, run by main",
            "  k3.py: 1 runs, avg 7 min, total 7 min, run by main",
        ])
        self.assertEqual([r for r in ("ack %d" % i for i in range(6)) if '"%s"' % r in text],
                         ["ack 1", "ack 2", "ack 3", "ack 4", "ack 5"])


if __name__ == "__main__":
    unittest.main()
