import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))

import gate_policy  # noqa: E402

HOOK = pathlib.Path(__file__).resolve().parents[2] / "hooks" / "plan-simplify-gate.py"
PARAMS = {"plan_simplify": {"fan_out_min_words": 100, "fan_out_min_agents": 2}}


class PlanSimplifyDecisionTest(unittest.TestCase):
    def test_review_older_than_plan_is_refused(self):
        reason = gate_policy.plan_simplify_decision("/p/plans/a.md", 10, 50.0, 40.0, 0, PARAMS)
        self.assertIn("/p/plans/a.simplify.md", reason)

    def test_one_refusal_names_both_unmet_conditions(self):
        reason = gate_policy.plan_simplify_decision("/p/plans/a.md", 100, 50.0, None, 1, PARAMS)
        self.assertIn("/p/plans/a.simplify.md", reason)
        self.assertIn("found 1 agents", reason)

    def test_small_plan_with_fresh_review_passes_without_agents(self):
        self.assertIsNone(gate_policy.plan_simplify_decision("/p/plans/a.md", 99, 50.0, 60.0, 0, PARAMS))

    def test_large_plan_needs_the_agent_count(self):
        reason = gate_policy.plan_simplify_decision("/p/plans/a.md", 100, 50.0, 60.0, 1, PARAMS)
        self.assertIn("found 1 agents at spawn depth 1", reason)
        self.assertIsNone(gate_policy.plan_simplify_decision("/p/plans/a.md", 100, 50.0, 60.0, 2, PARAMS))

    def test_defaults_come_from_the_params_file(self):
        with open(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "orchestrate-params.json")) as fh:
            rule = json.load(fh)["plan_simplify"]
        self.assertEqual(
            {k: v for k, v in rule.items() if not k.startswith("_")},
            gate_policy.DEFAULT_PARAMS["plan_simplify"],
        )


class PlanSimplifyHookTest(unittest.TestCase):
    """End to end: the hook as a subprocess over a temp transcript directory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.plans = root / "plans"
        self.plans.mkdir()
        self.project = root / "project"
        self.subagents = self.project / "sess" / "subagents"
        self.subagents.mkdir(parents=True)
        self.transcript = self.project / "sess.jsonl"
        self.agents = 0

    def tearDown(self):
        self.tmp.cleanup()

    def write_plan(self, name, words):
        """Writes like the harness: a new file replaces the old inode."""
        plan = self.plans / name
        draft = self.plans / (name + ".tmp")
        draft.write_text(" ".join(["word"] * words))
        os.replace(draft, plan)
        line = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Write", "input": {"file_path": str(plan)}}]}}
        with open(self.transcript, "a") as fh:
            fh.write(json.dumps(line) + "\n")
        (self.plans / (name[:-3] + ".simplify.md")).write_text("findings")
        return plan

    def add_agent(self, prompt, depth=1):
        self.agents += 1
        path = self.subagents / ("agent-a%d.jsonl" % self.agents)
        path.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": prompt}}) + "\n")
        (self.subagents / ("agent-a%d.meta.json" % self.agents)).write_text(json.dumps({"spawnDepth": depth}))

    def run_hook(self):
        payload = {"session_id": "sess", "transcript_path": str(self.transcript), "tool_name": "ExitPlanMode"}
        return subprocess.run(["python3", str(HOOK)], input=json.dumps(payload), capture_output=True, text=True)

    def test_small_plan_with_fresh_review_passes(self):
        self.write_plan("small.md", 200)
        self.assertEqual(self.run_hook().returncode, 0)

    def test_large_plan_with_one_lens_agent_is_refused(self):
        plan = self.write_plan("big.md", 1300)
        self.add_agent("lens: reuse. Plan: %s" % plan)
        result = self.run_hook()
        self.assertEqual(result.returncode, 2)
        self.assertIn("1300 words", result.stderr)
        self.assertIn("found 1 agents", result.stderr)

    def test_large_plan_with_two_lens_agents_passes(self):
        plan = self.write_plan("big.md", 1300)
        self.add_agent("lens: reuse. Plan: %s" % plan)
        self.add_agent("lens: premises. Plan: %s" % plan)
        self.assertEqual(self.run_hook().returncode, 0)

    def test_rewritten_plan_keeps_its_lens_agents(self):
        plan = self.write_plan("big.md", 1300)
        self.add_agent("lens: reuse. Plan: %s" % plan)
        self.add_agent("lens: premises. Plan: %s" % plan)
        self.write_plan("big.md", 1310)
        self.assertEqual(self.run_hook().returncode, 0)

    def test_agent_naming_another_plan_is_not_counted(self):
        self.write_plan("other.md", 10)
        plan = self.write_plan("big.md", 1300)
        self.add_agent("lens: reuse. Plan: %s" % (self.plans / "other.md"))
        self.add_agent("lens: premises. Plan: %s" % plan)
        self.assertIn("found 1 agents", self.run_hook().stderr)

    def test_nested_agent_is_not_counted(self):
        plan = self.write_plan("big.md", 1300)
        self.add_agent("search for %s" % plan, depth=2)
        self.add_agent("lens: premises. Plan: %s" % plan)
        self.assertIn("found 1 agents", self.run_hook().stderr)

    def test_agent_draft_does_not_replace_the_session_plan(self):
        plan = self.write_plan("big.md", 1300)
        self.write_plan("big-agent-a1.md", 10)
        self.add_agent("lens: reuse. Plan: %s" % plan)
        self.assertIn("big.md: the plan has 1300 words", self.run_hook().stderr)

    def test_plan_slug_containing_agent_is_still_gated(self):
        plan = self.write_plan("review-agent-flow.md", 1300)
        self.add_agent("lens: reuse. Plan: %s" % plan)
        self.assertEqual(self.run_hook().returncode, 2)

    def test_session_without_a_plan_passes(self):
        self.transcript.write_text("")
        self.assertEqual(self.run_hook().returncode, 0)


if __name__ == "__main__":
    unittest.main()
