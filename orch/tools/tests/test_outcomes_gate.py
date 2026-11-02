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

    def test_launch_stopped_or_awaited_later_in_the_call_is_allowed(self):
        for command in ("./server & sleep 2; curl localhost:8080; kill $!",
                        "./server & sleep 1; ./client; wait",
                        "python3 tmp/x.py & P=$!; sleep 5; kill \"$P\"",
                        "python3 tmp/x.py & sleep 5; pkill -f tmp/x.py"):
            with self.subTest(command=command):
                self.assertEqual(decide(command), "allow")

    def test_kill_before_the_launch_or_of_another_job_does_not_exempt(self):
        for command in ("pkill -f s2_chain; nohup sh scripts/s2_chain.sh > log 2>&1 &",
                        "kill $(cat tmp/pid); nohup python3 tmp/train.py &",
                        "python3 tmp/train.py > log 2>&1 &\nsleep 3; kill -0 $! && echo alive",
                        "python3 tmp/train.py & ./server & sleep 1; kill $!"):
            with self.subTest(command=command):
                self.assertEqual(decide(command), "deny")

    def test_launch_inside_a_pipe_subshell_brace_group_or_loop_is_refused(self):
        for command in ("python3 train.py 2>&1 | tee train.log &",
                        "(cd tmp && python3 run.py) > log 2>&1 &",
                        "{ python3 tmp/a.py; echo done; } > log 2>&1 &",
                        "while true; do python3 tmp/a.py; sleep 60; done &"):
            with self.subTest(command=command):
                self.assertEqual(decide(command), "deny")

    def test_launch_under_wrappers_is_refused(self):
        for command in ("caffeinate -i python3 train.py > log 2>&1 &",
                        "timeout 3600 python3 train.py &",
                        "nohup nice -n 10 python3 x.py &",
                        "sudo ./scripts/x.sh &",
                        "setsid -f ./train.sh",
                        "nohup rtk proxy python3 tmp/x.py &",
                        "poetry run python scripts/batch.py &",
                        "uv run scripts/x.py &",
                        "nohup uv run --python 3.12 scripts/x.py &",
                        "python3 -W ignore train.py &"):
            with self.subTest(command=command):
                self.assertEqual(decide(command), "deny")

    def test_detached_services_are_allowed_without_the_line(self):
        for command in ("L=tmp/logs nohup kubectl port-forward svc/a 8080:80 > tmp/pf.log 2>&1 &",
                        "node_modules/.bin/vite --port 5173 > tmp/vite.log 2>&1 &"):
            with self.subTest(command=command):
                self.assertEqual(decide(command), "allow")

    def test_detached_scripts_are_refused_without_the_line(self):
        for command in ("nohup python3 tmp/x.py > tmp/x.log 2>&1 &",
                        "./scripts/run.sh > log 2>&1 &"):
            with self.subTest(command=command):
                self.assertEqual(decide(command), "deny")

    def test_background_call_without_the_line_is_refused(self):
        self.assertEqual(decide("python3 train.py", background=True), "deny")

    def test_and_list_and_redirections_are_not_detach(self):
        self.assertEqual(decide("make build && ./run.sh 2>&1 >&2 | tee log"), "allow")

    def test_amp_inside_quotes_or_heredoc_is_not_detach(self):
        self.assertEqual(decide("grep -c ' & ' f\ncat <<EOF\na &\nEOF"), "allow")

    def test_pure_wait_loop_is_allowed_without_the_line(self):
        for command, background in (("until grep -q done log; do sleep 5; done", True),
                                    ("until ./check.sh; do sleep 30; done", True),
                                    ("until ./check.sh; do sleep 30; done &", False)):
            with self.subTest(command=command):
                self.assertEqual(decide(command, background=background), "allow")

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
