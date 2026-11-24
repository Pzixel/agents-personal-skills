"""Rule `parts`: a main-session builder prompt declares the task's parts on one `parts:` line, and over
`parallel.split_above_minutes` a builder owns one part. Checked through ``spawn_gate_decision``, the one point
every builder spawn passes. Prompts are synthetic, in the shape of a step 1 prompt of the orchestrate skill."""
import importlib.util
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))

import gate_policy  # noqa: E402

BRIEF = "task: add a nightly report\nworkspace: /r/.worktrees/report-engine\nfiles: a.py:10\ncheck: make test\n"
SIX_PARTS = ("parts: 210 min; engine; client; flag; store; scheduler after engine, client, flag, store; "
             "report after scheduler; mine: {mine}")


def spawn(prompt, params=None):
    return gate_policy.spawn_gate_decision("", "orch:builder", "", prompt, params)


class PartsLineTest(unittest.TestCase):
    def test_missing_line_is_refused_with_the_one_line_grammar(self):
        decision = spawn(BRIEF)
        self.assertEqual(decision["decision"], "deny")
        self.assertIn("`parts: <N> min; <name>[ after <name>, ...]; ...; mine: <name>`", decision["reason"])
        self.assertIn("ONE line", decision["reason"])
        self.assertIn("letters, digits, `.`, `_` and `-`", decision["reason"])

    def test_one_part_of_a_plan_over_the_threshold_is_allowed(self):
        self.assertEqual(spawn(BRIEF + SIX_PARTS.format(mine="engine"))["decision"], "allow")

    def test_unsplit_task_is_allowed(self):
        self.assertEqual(spawn(BRIEF + "parts: 240 min; whole; mine: whole")["decision"], "allow")

    def test_owning_several_parts_depends_on_the_estimate(self):
        for minutes, expected in ((61, "deny"), (60, "allow"), (45, "allow")):
            with self.subTest(minutes=minutes):
                prompt = BRIEF + "parts: %d min; engine; report after engine; mine: engine, report" % minutes
                self.assertEqual(spawn(prompt)["decision"], expected)

    def test_threshold_comes_from_the_params_file(self):
        prompt = BRIEF + SIX_PARTS.format(mine="engine, client")
        self.assertEqual(spawn(prompt, {"parallel": {"split_above_minutes": 300}})["decision"], "allow")

    def test_owner_of_a_several_part_refusal_says_one_builder_per_part(self):
        decision = spawn(BRIEF + SIX_PARTS.format(mine="engine, client"))
        self.assertEqual(decision["decision"], "deny")
        self.assertIn("own worktree, branch and builder, all spawned in one message", decision["reason"])

    def test_owned_part_must_be_declared(self):
        decision = spawn(BRIEF + SIX_PARTS.format(mine="engin"))
        self.assertEqual(decision["decision"], "deny")
        self.assertIn("`engin`", decision["reason"])

    def test_line_wrapped_onto_a_second_line_is_refused(self):
        prompt = BRIEF + "parts: 210 min; engine; client;\n  report after engine, client; mine: engine"
        self.assertEqual(spawn(prompt)["decision"], "deny")

    def test_names_take_the_worktree_class(self):
        prompt = BRIEF + "parts: 90 min; TICKET-12.api; ui_v2 after TICKET-12.api; mine: ui_v2"
        self.assertEqual(spawn(prompt)["decision"], "allow")

    def test_handoff_line_is_exempt_but_handoff_in_prose_is_not(self):
        self.assertEqual(spawn(BRIEF + "handoff: abc123, /n/review-verified.md")["decision"], "allow")
        self.assertEqual(spawn(BRIEF + "note: no handoff: needed")["decision"], "deny")


class DeclaredCheckOrderTest(unittest.TestCase):
    def test_parts_line_after_check_is_not_part_of_the_check(self):
        hook = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "hooks", "checks-before-done.py")
        spec = importlib.util.spec_from_file_location("checks_before_done", hook)
        module = importlib.util.module_from_spec(spec)
        with mock.patch("sys.stdin", io.StringIO("")), self.assertRaises(SystemExit):
            spec.loader.exec_module(module)  # the hook runs on import and exits 0 on an empty payload
        entry = {"type": "user", "message": {"content": BRIEF + SIX_PARTS.format(mine="engine")}}
        self.assertEqual(module.declared_check([entry]), "make test")


if __name__ == "__main__":
    unittest.main()
