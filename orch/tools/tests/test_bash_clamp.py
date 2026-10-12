import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))

import gate_policy  # noqa: E402
from clamp import clamp  # noqa: E402

PARAMS = {"bash_clamp": {"types": ["builder"], "head_lines": 2, "tail_lines": 3, "max_bytes": 8000}}


def numbered(n):
    return "".join("line%02d\n" % i for i in range(1, n + 1))


class ClampTest(unittest.TestCase):
    def test_text_within_limit_is_unchanged(self):
        self.assertEqual(clamp(numbered(50), 2, 3, 10000, "/o.log"), numbered(50))

    def test_over_limit_keeps_head_marker_tail(self):
        out = clamp(numbered(20), 2, 3, 120, "/o.log")
        self.assertEqual(
            out,
            "line01\nline02\n[... 15 lines, 0.1 KB omitted; full output: /o.log]\nline18\nline19\nline20\n",
        )

    def test_head_is_cut_first_when_still_over(self):
        out = clamp(numbered(20), 2, 3, 80, "/o.log")
        self.assertEqual(
            out,
            "line01\n[... 16 lines, 0.1 KB omitted; full output: /o.log]\nline18\nline19\nline20\n",
        )


class BashClampDecisionTest(unittest.TestCase):
    def decide(self, command, agent_type="orch:builder"):
        return gate_policy.bash_clamp_decision(agent_type, command, PARAMS, "/t/o 1.log", "/tools")

    def test_unlisted_type_is_left_alone(self):
        self.assertIsNone(self.decide("pytest", agent_type="reviewer"))

    def test_handoff_and_wait_for_stay_bare(self):
        self.assertIsNone(self.decide("bash ~/.claude/hooks/handoff.sh"))
        self.assertIsNone(self.decide("~/.claude/tools/wait-for --every 30 --max 570 -- true"))

    def test_already_clamped_is_left_alone(self):
        self.assertIsNone(self.decide(self.decide("pytest")))

    def test_wrapped_command(self):
        self.assertEqual(
            self.decide("cd x && pytest"),
            "{ cd x && pytest\n} > '/t/o 1.log' 2>&1; __orch_rc=$?; "
            "python3 /tools/clamp.py '/t/o 1.log' --head 2 --tail 3 --max-bytes 8000; ( exit $__orch_rc )",
        )


class OwnTranscriptPathTest(unittest.TestCase):
    def test_both_payload_shapes_resolve_to_the_subagent_file(self):
        own = "/p/s1/subagents/agent-a1.jsonl"
        self.assertEqual(gate_policy.own_transcript_path(own, ""), own)
        self.assertEqual(gate_policy.own_transcript_path("/p/s1.jsonl", "a1"), own)
        self.assertEqual(gate_policy.own_transcript_path("/p/s1.jsonl", ""), "")


if __name__ == "__main__":
    unittest.main()
