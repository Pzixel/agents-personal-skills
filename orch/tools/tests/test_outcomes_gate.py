import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import gate_policy  # noqa: E402

# The 171ba373 relaunch (2026-11-01 21:44:29Z), heredoc shortened.
RELAUNCH = (
    "D=/tmp/emb-exp; python3 - <<EOF\nimport json\nprint(1 & 2)\nEOF\n"
    "cd $D && echo \"== restart $(date)\" >> chain.log && "
    "nohup sh scripts/s2_chain.sh >> chain.log 2>&1 < /dev/null & \n"
    "sleep 3; pgrep -fl \"s2_chain.sh|ft_harrier\" | cut -c1-150"
)
DISTINCT = "# OUTCOMES: speed under gate -> run eval; speed over gate -> drop the model\n"


def decide(command, background=False):
    return gate_policy.outcomes_decision(command, background)["decision"]


class OutcomesTriggerTest(unittest.TestCase):
    def test_detached_relaunch_without_the_line_is_refused(self):
        self.assertEqual(decide(RELAUNCH), "deny")

    def test_detached_launch_paced_by_a_poll_length_sleep_is_refused(self):
        self.assertEqual(decide("nohup ./train.sh > log 2>&1 &\nsleep 5; tail -3 log"), "deny")

    def test_inline_background_amp_mid_command_is_refused(self):
        self.assertEqual(decide("./server & sleep 2; curl localhost:8080"), "deny")

    def test_other_detach_launchers_are_refused(self):
        for command in ("setsid ./train.sh", "tmux new -d -s t ./train.sh",
                        "screen -dmS t ./train.sh", "./train.sh & disown"):
            with self.subTest(command=command):
                self.assertEqual(decide(command), "deny")

    def test_detached_helper_stopped_in_the_same_call_is_allowed(self):
        for command in ("kubectl port-forward svc/a 8080:80 & sleep 2; curl localhost:8080; kill $!",
                        "./server & sleep 1; ./client; wait"):
            with self.subTest(command=command):
                self.assertEqual(decide(command), "allow")

    def test_background_call_without_the_line_is_refused(self):
        self.assertEqual(decide("python3 train.py", background=True), "deny")

    def test_long_foreground_call_is_not_a_trigger(self):
        self.assertEqual(decide("python3 -m pytest -q"), "allow")

    def test_and_list_and_redirections_are_not_detach(self):
        self.assertEqual(decide("make build && ./run.sh 2>&1 >&2 | tee log"), "allow")

    def test_amp_inside_quotes_or_heredoc_is_not_detach(self):
        self.assertEqual(decide("grep -c ' & ' f\ncat <<EOF\na &\nEOF"), "allow")

    def test_background_pure_wait_loop_is_allowed_without_the_line(self):
        self.assertEqual(decide("until grep -q done log; do sleep 5; done", background=True), "allow")

    def test_background_wait_for_is_allowed_without_the_line(self):
        self.assertEqual(decide("~/.claude/tools/wait-for --every 30 --max 270 -- test -f x",
                                background=True), "allow")

    def test_acknowledgements_do_not_exempt(self):
        command = "# NO_POLL_OVERRIDE: my own chain restarts\n# LOOP_COST: one full run\n" + RELAUNCH
        self.assertEqual(decide(command), "deny")


class OutcomesLineTest(unittest.TestCase):
    def test_two_distinct_steps_allow(self):
        self.assertEqual(decide(DISTINCT + RELAUNCH), "allow")

    def test_steps_identical_after_normalisation_are_refused(self):
        line = "# OUTCOMES: fast -> Run  Eval; slow -> run eval\n"
        self.assertEqual(decide(line + RELAUNCH), "deny")

    def test_a_single_outcome_is_refused(self):
        self.assertEqual(decide("# OUTCOMES: done -> report\n" + RELAUNCH), "deny")

    def test_pairs_missing_a_step_do_not_count(self):
        self.assertEqual(decide("# OUTCOMES: fast -> report; slow ->\n" + RELAUNCH), "deny")


if __name__ == "__main__":
    unittest.main()
