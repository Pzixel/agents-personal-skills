import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))

import gate_policy  # noqa: E402

HOOK = pathlib.Path(__file__).resolve().parents[2] / "hooks" / "plan-simplify-gate.py"
with open(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "orchestrate-params.json")) as _fh:
    PARAMS_FILE = json.load(_fh)["plan_simplify"]
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


class DesignGateTargetTest(unittest.TestCase):
    PROMPT = "task: x\nworkspace: /w (branch y)\n"

    def test_proceed_to_code_to_a_builder_is_a_fresh_design_gate(self):
        target = gate_policy.design_gate_target(
            "SendMessage", {"message": "Design reviewed. Proceed to code."}, "orch:builder", self.PROMPT)
        self.assertEqual(target, ("/w/orch-notes/DESIGN.md", "design"))

    def test_size_return_message_mentioning_the_phrase_is_not_gated(self):
        self.assertIsNone(gate_policy.design_gate_target(
            "SendMessage", {"message": "size 4x: back to the design, do not proceed to code yet"},
            "orch:builder", self.PROMPT))

    def test_phrase_without_the_design_reviewed_start_is_not_gated(self):
        self.assertIsNone(gate_policy.design_gate_target(
            "SendMessage", {"message": "proceed to code"}, "orch:builder", self.PROMPT))

    def test_proceed_to_code_to_a_reviewer_is_not_gated(self):
        self.assertIsNone(gate_policy.design_gate_target(
            "SendMessage", {"message": "design reviewed, proceed to code"}, "orch:reviewer", self.PROMPT))

    def test_other_message_to_a_builder_is_not_gated(self):
        self.assertIsNone(gate_policy.design_gate_target(
            "SendMessage", {"message": "fix round 1"}, "orch:builder", self.PROMPT))

    def test_handoff_builder_spawn_is_an_existence_gate(self):
        target = gate_policy.design_gate_target(
            "Agent", {"subagent_type": "builder", "prompt": self.PROMPT + "handoff: /w/orch-notes/HANDOFF.md"})
        self.assertEqual(target, ("/w/orch-notes/DESIGN.md", "handoff"))

    def test_builder_spawn_without_handoff_is_not_gated(self):
        self.assertIsNone(gate_policy.design_gate_target(
            "Agent", {"subagent_type": "orch:builder", "prompt": self.PROMPT}))

    def test_builder_prompt_without_workspace_line_passes(self):
        self.assertIsNone(gate_policy.design_gate_target(
            "SendMessage", {"message": "design reviewed, proceed to code"}, "orch:builder", "task: x"))


class HandoffDecisionTest(unittest.TestCase):
    def test_handoff_with_simplify_file_older_than_design_passes(self):
        self.assertIsNone(gate_policy.plan_simplify_decision(
            "/w/orch-notes/DESIGN.md", 10, 50.0, 40.0, 0, PARAMS, "handoff"))

    def test_handoff_without_simplify_file_is_refused(self):
        reason = gate_policy.plan_simplify_decision("/w/orch-notes/DESIGN.md", 10, 50.0, None, 0, PARAMS, "handoff")
        self.assertIn("/w/orch-notes/DESIGN.simplify.md is missing", reason)


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


class DesignSimplifyHookTest(unittest.TestCase):
    """SendMessage `proceed to code` and a builder spawn from a handoff, over temp files."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.ws = root / "ws"
        (self.ws / "orch-notes").mkdir(parents=True)
        self.design = self.ws / "orch-notes" / "DESIGN.md"
        self.subagents = root / "project" / "sess" / "subagents"
        self.subagents.mkdir(parents=True)
        self.transcript = root / "project" / "sess.jsonl"
        self.transcript.write_text("")

    def tearDown(self):
        self.tmp.cleanup()

    def agent(self, agent_id, prompt, agent_type="orch:builder"):
        (self.subagents / ("agent-%s.jsonl" % agent_id)).write_text(
            json.dumps({"type": "user", "message": {"role": "user", "content": prompt}}) + "\n")
        (self.subagents / ("agent-%s.meta.json" % agent_id)).write_text(
            json.dumps({"agentType": agent_type, "spawnDepth": 1}))

    def run_hook(self, tool, tool_input):
        payload = {"session_id": "sess", "transcript_path": str(self.transcript),
                   "tool_name": tool, "tool_input": tool_input}
        return subprocess.run(["python3", str(HOOK)], input=json.dumps(payload), capture_output=True, text=True)

    def proceed(self, message="design reviewed, proceed to code"):
        return self.run_hook("SendMessage", {"to": "b1", "message": message})

    def handoff(self):
        prompt = "task: x\nworkspace: %s\nhandoff: notes/HANDOFF.md" % self.ws
        return self.run_hook("Agent", {"subagent_type": "orch:builder", "prompt": prompt})

    def simplify(self, text="findings", age=0):
        path = self.ws / "orch-notes" / "DESIGN.simplify.md"
        path.write_text(text)
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))

    def test_design_without_simplify_file_is_refused(self):
        self.agent("b1", "task: x\nworkspace: %s (branch y)" % self.ws)
        self.design.write_text("design")
        result = self.proceed()
        self.assertEqual(result.returncode, 2)
        self.assertIn(str(self.ws / "orch-notes" / "DESIGN.simplify.md"), result.stderr)

    def test_design_with_fresh_simplify_file_passes(self):
        self.agent("b1", "workspace: %s" % self.ws)
        self.design.write_text("design")
        self.simplify()
        self.assertEqual(self.proceed().returncode, 0)

    def test_design_with_fresh_empty_simplify_file_passes(self):
        self.agent("b1", "workspace: %s" % self.ws)
        self.design.write_text("design")
        self.simplify("")
        self.assertEqual(self.proceed().returncode, 0)

    def test_design_replaced_after_the_pass_is_refused(self):
        self.agent("b1", "workspace: %s" % self.ws)
        self.design.write_text("design")
        self.simplify(age=100)
        fresh = self.design.with_name("DESIGN.tmp")
        fresh.write_text("design v2")
        os.replace(fresh, self.design)
        self.assertEqual(self.proceed().returncode, 2)

    def test_capitalized_go_ahead_on_a_stale_design_is_refused(self):
        self.agent("b1", "workspace: %s" % self.ws)
        self.design.write_text("design")
        self.simplify(age=100)
        self.assertEqual(self.proceed("Design reviewed, Proceed to code.").returncode, 2)

    def test_design_edited_in_place_after_the_pass_is_refused(self):
        self.agent("b1", "workspace: %s" % self.ws)
        self.design.write_text("design")
        self.simplify()
        with open(self.design, "a") as fh:
            fh.write(" v2")
        later = time.time() + 100
        os.utime(self.design, (later, later))
        self.assertEqual(self.proceed().returncode, 2)

    def test_large_design_counts_only_simplify_lens_agents(self):
        self.agent("b1", "workspace: %s" % self.ws)
        self.design.write_text(" ".join(["word"] * 1300))
        self.simplify()
        self.agent("r1", "mode: review\nReview %s" % self.design, "orch:reviewer")
        self.agent("r2", "/simplify lens reuse on %s" % self.design, "orch:reviewer")
        result = self.proceed()
        self.assertEqual(result.returncode, 2)
        self.assertIn("at least %d lens agents" % PARAMS_FILE["fan_out_min_agents"], result.stderr)

    def test_large_design_with_enough_lens_agents_passes(self):
        self.agent("b1", "workspace: %s" % self.ws)
        self.design.write_text(" ".join(["word"] * 1300))
        self.simplify()
        self.agent("r1", "/simplify lens reuse on %s" % self.design, "orch:reviewer")
        self.agent("r2", "/simplify lens premises on %s" % self.design, "orch:reviewer")
        self.assertEqual(self.proceed().returncode, 0)

    def test_without_design_file_passes(self):
        self.agent("b1", "workspace: %s" % self.ws)
        self.assertEqual(self.proceed().returncode, 0)

    def test_handoff_at_the_design_stage_is_refused(self):
        self.design.write_text("design")
        self.assertEqual(self.handoff().returncode, 2)

    def test_handoff_after_the_pass_and_a_later_design_write_passes(self):
        self.design.write_text("design")
        self.simplify(age=100)
        self.design.write_text("design with the findings applied")
        self.assertEqual(self.handoff().returncode, 0)

    def test_hooks_json_routes_all_three_tools_to_the_gate(self):
        with open(HOOK.parent / "hooks.json") as fh:
            entries = json.load(fh)["hooks"]["PreToolUse"]
        matchers = [e["matcher"] for e in entries if any("plan-simplify-gate.py" in h["command"] for h in e["hooks"])]
        for tool in ("ExitPlanMode", "SendMessage", "Agent"):
            self.assertTrue(any(re.fullmatch(m, tool) for m in matchers), tool)


if __name__ == "__main__":
    unittest.main()
