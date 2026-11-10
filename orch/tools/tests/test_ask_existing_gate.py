import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import gate_policy  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[3]
HOOK = ROOT / "orch" / "hooks" / "ask-existing-gate.py"


def run_hook(stdin):
    return subprocess.run([sys.executable, str(HOOK)], input=stdin, capture_output=True, text=True, timeout=20)


class AskExistingHookTest(unittest.TestCase):
    def payload(self, tool_input):
        return json.dumps({"tool_name": "AskUserQuestion", "tool_input": tool_input})

    def test_refusal_goes_to_stderr_with_exit_2(self):
        tool_input = {"questions": [{"question": "Which count?", "options": []}]}
        out = run_hook(self.payload(tool_input))
        self.assertEqual((out.returncode, out.stderr), (2, gate_policy.ask_existing_decision(tool_input) + "\n"))

    def test_spend_question_passes_and_is_logged_when_the_judge_cannot_run(self):
        tool_input = {"questions": [{"question": "Buy 39 domains for 507 USD?", "options": []}],
                      "metadata": {"source": "existing: n/a"}}
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, ".claude"))
            env = dict(os.environ, HOME=home, PATH=os.path.dirname(sys.executable))
            out = subprocess.run([sys.executable, str(HOOK)], input=self.payload(tool_input), capture_output=True,
                                 text=True, timeout=20, env=env)
            with open(os.path.join(home, ".claude", "ask-existing-gate-log.jsonl")) as fh:
                row = json.loads(fh.read())
        self.assertEqual(out.returncode, 0)
        self.assertEqual((row["judged"], row["fired"], row["error"]), (False, False, "FileNotFoundError"))

    def test_input_that_is_not_json_passes(self):
        self.assertEqual(run_hook("not json").returncode, 0)

    def test_hooks_json_routes_ask_user_question_to_the_hook(self):
        entries = json.loads((ROOT / "orch" / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"]
        routed = [h["command"] for e in entries if "AskUserQuestion" in e["matcher"].split("|") for h in e["hooks"]]
        self.assertTrue(any("ask-existing-gate.py" in command for command in routed), routed)


if __name__ == "__main__":
    unittest.main()
